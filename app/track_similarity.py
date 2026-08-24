# app/track_similarity.py 14-08-25 01-50
"""Функции и классы для поиска похожих треков на основе жанра, confidence и audio features."""
import os
import sqlite3
import random
import logging
from typing import Optional, List, Dict, Tuple

import numpy as np

from .config import load_config, DEFAULT_CONFIG
from .db import SCAN_DB, load_scan_result, load_scan_result_extended
from .utils import normalize_audio_filename
# Логирование
from .logging_config import (
    is_log_type_enabled,
    setup_model_logger,
    # (другие setup_ если понадобятся)
)

# model logger
model_logger = logging.getLogger("model")
setup_model_logger()
logger = logging.getLogger(__name__)


class TrackSimilarityFinder:
    """
    Класс для поиска похожих треков на основе жанра, confidence и audio features.
    """

    def __init__(self, db_path: str = SCAN_DB):
        self.db_path = db_path

    def find_similar_track(self, current_track_path: str, current_genre: str = None, use_model: bool = False) -> Optional[Dict]:
        """
        Находит похожий трек на основе текущего трека.

        Args:
            current_track_path: путь к текущему треку
            current_genre: жанр текущего трека (опционально)

        Returns:
            Dict с информацией о рекомендованном треке или None
        """
        logger.info(
            "Поиск похожего трека для: %s, жанр: %s",
            current_track_path, current_genre
        )
        if use_model:
            # ВСЕГДА использовать поиск через модель (анализировать аудио)
            from .models import get_genre
            from .utils import safe_join_music_dir
            from .config import load_config, DEFAULT_CONFIG

            config = load_config()
            music_dir = config.get("music_dir", DEFAULT_CONFIG["music_dir"])
            normalized_path = safe_join_music_dir(music_dir, current_track_path)
            genre_meta = get_genre(normalized_path, return_meta=True)
            if len(genre_meta) == 4:
                genre, confidence, features, meta_vectors = genre_meta
            else:
                genre, confidence, features = genre_meta
                meta_vectors = None
            if genre.lower() == "unknown" or features is None:
                logger.warning("Не удалось получить признаки через модель для трека %s", current_track_path)
                return None
            search_genre = genre

            # Выбор режима схожести:
            # 1) Если есть fused_proba из meta_vectors (live) и в БД у кандидатов тоже есть fused_proba — используем её.
            current_fused_live = None
            if meta_vectors and meta_vectors.get("fused_proba"):
                current_fused_live = meta_vectors["fused_proba"]
            candidates = self._find_candidates(
                current_track_path,
                search_genre,
                confidence,
                features
            )
            if not candidates:
                logger.info("Похожие треки не найдены")
                return None
            current_rf_live = meta_vectors.get("rf_proba") if meta_vectors else None
            recommended_track = self._select_best_candidate(
                candidates, features, current_fused=current_fused_live, current_rf=current_rf_live
            )
            if recommended_track:
                logger.info(f"Рекомендован трек: {recommended_track['path']}")
                return {
                    "redirect": recommended_track["path"],
                    "filename": os.path.basename(recommended_track["path"]),
                    "folder": os.path.dirname(recommended_track["path"]),
                    "genre": recommended_track["genre"],
                    "confidence": recommended_track["confidence"],
                    "similarity_score": recommended_track.get("similarity_score", 0)
                }
            return None
        # Получаем информацию о текущем треке из базы
        current_track_info = self._get_track_info(current_track_path)
        if not current_track_info:
            logger.warning(f"Информация о треке {current_track_path} не найдена в базе")
            return None

        genre, confidence, features = current_track_info

        # Используем переданный жанр или жанр из базы
        search_genre = current_genre or genre
        if not search_genre or search_genre.lower() == "unknown":
            logger.warning("Жанр трека неизвестен, поиск невозможен")
            return None

        # Ищем кандидатов
        candidates = self._find_candidates(
            current_track_path,
            search_genre,
            confidence,
            features
        )

        if not candidates:
            logger.info("Похожие треки не найдены")
            return None

        # Выбираем лучший трек
        recommended_track = self._select_best_candidate(candidates, features, current_fused=None, current_rf=None)

        if recommended_track:
            logger.info(f"Рекомендован трек: {recommended_track['path']}")
            return {
                "redirect": recommended_track["path"],
                "filename": os.path.basename(recommended_track["path"]),
                "folder": os.path.dirname(recommended_track["path"]),
                "genre": recommended_track["genre"],
                "confidence": recommended_track["confidence"],
                "similarity_score": recommended_track.get("similarity_score", 0)
            }

        return None

    def _get_track_info(self, track_path: str) -> Optional[Tuple]:
        """Получает информацию о треке из базы данных"""
        try:
            norm_path = os.path.normpath(track_path)
            row = load_scan_result(norm_path)
            if row and len(row) >= 4:
                genre, mtime, confidence, features_json = row
                return genre, confidence, features_json
            return None
        except Exception as e:
            logger.error(f"Ошибка получения информации о треке {track_path}: {e}")
            return None

    def _find_candidates(self, current_path: str, genre: str, confidence: float, features) -> List[Dict]:
        """Находит кандидатов для рекомендации"""
        try:
            con = sqlite3.connect(self.db_path)
            cur = con.cursor()

            # Основной запрос: треки того же жанра, исключая текущий
            query = """
                SELECT rel_path, genre, confidence, features
                FROM scan_results
                WHERE genre = ? AND rel_path != ? AND confidence IS NOT NULL
                ORDER BY confidence DESC
                LIMIT 50
            """

            cur.execute(query, (genre, current_path))
            rows = cur.fetchall()
            con.close()

            candidates = []
            norm_current = normalize_audio_filename(current_path)
            for row in rows:
                path, track_genre, track_confidence, track_features = row
                norm_candidate = normalize_audio_filename(path)
                if norm_candidate == norm_current:
                    if is_log_type_enabled("model"):
                        model_logger.debug(f"[similarity] Пропущен self-match: {norm_candidate}")
                    continue
                # Проверяем, что файл существует
                if self._track_exists(path):
                    candidates.append({
                        "path": path,
                        "genre": track_genre,
                        "confidence": track_confidence,
                        "features": track_features
                    })

            logger.info(f"Найдено {len(candidates)} кандидатов для жанра {genre}")
            return candidates

        except Exception as e:
            logger.error(f"Ошибка поиска кандидатов: {e}")
            return []

    def _track_exists(self, rel_path: str) -> bool:
        """Проверяет существование файла трека"""
        try:
            config = load_config()
            music_dir = config.get("music_dir", DEFAULT_CONFIG["music_dir"])
            full_path = os.path.join(music_dir, rel_path)
            return os.path.isfile(full_path)
        except:
            return False

    def _select_best_candidate(self, candidates: List[Dict], current_features,
                               current_fused=None, current_rf=None) -> Optional[Dict]:
        """
        Выбирает лучший трек из кандидатов на основе confidence и схожести features
        """
        if not candidates:
            return None

        # Фильтруем по минимальному confidence
        min_confidence = 0.4
        good_candidates = [c for c in candidates if c["confidence"] >= min_confidence]

        if not good_candidates:
            # Если нет треков с хорошим confidence, берем лучшие по confidence
            good_candidates = sorted(candidates, key=lambda x: x["confidence"], reverse=True)[:10]

        # Если есть features текущего трека, вычисляем схожесть
        use_fused = current_fused is not None
        if use_fused and good_candidates:
            # Используем fused_proba у кандидатов
            scored_candidates = []
            for cand in good_candidates:
                rf_vec, yam_vec, fused_vec = self._get_fused_vectors(cand["path"])
                if fused_vec and isinstance(fused_vec, list):
                    try:
                        a = np.array(current_fused, dtype=float)
                        b = np.array(fused_vec, dtype=float)
                        m = min(a.shape[0], b.shape[0])
                        sim = self._cosine_similarity(a[:m], b[:m])
                        cand_copy = cand.copy()
                        cand_copy["similarity_score"] = sim
                        scored_candidates.append(cand_copy)
                    except Exception as e:
                        if is_log_type_enabled("model"):
                            model_logger.debug(f"[similarity] fused calc error {cand['path']}: {e}")
                else:
                    # fallback — без fused у кандидата
                    cand_copy = cand.copy()
                    cand_copy["similarity_score"] = 0
                    scored_candidates.append(cand_copy)
            if scored_candidates:
                scored_candidates.sort(
                    key=lambda x: (x.get("similarity_score", 0) * 0.6 + x["confidence"] * 0.4),
                    reverse=True
                )
                return random.choice(scored_candidates[:5])
        elif current_fused is None and current_rf is not None and good_candidates:
            # Попытка RF-only similarity: если у кандидатов есть rf_proba
            scored_candidates = []
            cur_rf_arr = np.array(current_rf, dtype=float)
            for cand in good_candidates:
                rf_vec, yam_vec, fused_vec = self._get_fused_vectors(cand["path"])
                if rf_vec and isinstance(rf_vec, list):
                    try:
                        b = np.array(rf_vec, dtype=float)
                        m = min(cur_rf_arr.shape[0], b.shape[0])
                        sim = self._cosine_similarity(cur_rf_arr[:m], b[:m])
                        cand_copy = cand.copy()
                        cand_copy["similarity_score"] = sim
                        scored_candidates.append(cand_copy)
                    except Exception as e:
                        if is_log_type_enabled("model"):
                            model_logger.debug(f"[similarity] rf calc error {cand['path']}: {e}")
                else:
                    cand_copy = cand.copy()
                    cand_copy["similarity_score"] = 0
                    scored_candidates.append(cand_copy)
            if scored_candidates:
                scored_candidates.sort(
                    key=lambda x: (x.get("similarity_score", 0) * 0.55 + x["confidence"] * 0.45),
                    reverse=True
                )
                return random.choice(scored_candidates[:5])
        elif current_features is not None and np.size(current_features) > 0 and good_candidates:
            # Старый путь — схожесть по feature-вектору
            scored_candidates = self._calculate_similarity_scores(good_candidates, current_features)
            if scored_candidates:
                scored_candidates.sort(
                    key=lambda x: (x.get("similarity_score", 0) * 0.6 + x["confidence"] * 0.4),
                    reverse=True
                )
                return random.choice(scored_candidates[:5])
        elif current_features is not None and np.size(current_features) > 0 and good_candidates:
            scored_candidates = self._calculate_similarity_scores(good_candidates, current_features)
            if scored_candidates:
                scored_candidates.sort(
                    key=lambda x: (x.get("similarity_score", 0) * 0.6 + x["confidence"] * 0.4),
                    reverse=True
                )
                return random.choice(scored_candidates[:5])
            if scored_candidates:
                # Сортируем по комбинации confidence и similarity
                scored_candidates.sort(
                    key=lambda x: (x.get("similarity_score", 0) * 0.6 + x["confidence"] * 0.4),
                    reverse=True
                )
                return random.choice(scored_candidates[:5])  # Выбираем из топ-5

        # Если нет features или не удалось вычислить схожесть, выбираем по confidence
        return random.choice(good_candidates[:10])

    def _calculate_similarity_scores(self, candidates: List[Dict], current_features) -> List[Dict]:
        """Вычисляет scores схожести на основе audio features"""
        try:
            if current_features is None or np.size(current_features) == 0:
                return candidates

            # Парсим features текущего трека
            if isinstance(current_features, str):
                import json
                current_features = json.loads(current_features)

            if not isinstance(current_features, (list, np.ndarray)):
                return candidates

            current_features = np.array(current_features)
            scored_candidates = []

            for candidate in candidates:
                try:
                    # Парсим features кандидата
                    candidate_features = candidate["features"]
                    if isinstance(candidate_features, str):
                        import json
                        candidate_features = json.loads(candidate_features)
                    if not isinstance(candidate_features, (list, np.ndarray)) or np.size(candidate_features) == 0:
                        continue

                    candidate_features = np.array(candidate_features)

                    # Проверяем совместимость размерности
                    current_features = np.array(current_features).flatten()
                    candidate_features = np.array(candidate_features).flatten()
                    if current_features.shape != candidate_features.shape:
                        # Приводим к одинаковой длине
                        min_len = min(len(current_features), len(candidate_features))
                        current_features_norm = current_features[:min_len]
                        candidate_features_norm = candidate_features[:min_len]
                        if is_log_type_enabled("model"):
                            model_logger.debug(
                                f"[SIMILARITY-DEBUG][shape] Привели к min_len={min_len}, shapes после: {current_features_norm.shape}, {candidate_features_norm.shape}"
                            )
                    else:
                        current_features_norm = current_features
                        candidate_features_norm = candidate_features

                    # Вычисляем косинусное сходство
                    if is_log_type_enabled("model"):
                        model_logger.debug(
                            f"[SIMILARITY-DEBUG][before] current_features_norm[:5]: {current_features_norm[:5]}, "
                            f"candidate_features_norm[:5]: {candidate_features_norm[:5]}, "
                            f"shapes: {current_features_norm.shape}, {candidate_features_norm.shape}"
                        )
                    similarity = self._cosine_similarity(current_features_norm, candidate_features_norm)
                    if is_log_type_enabled("model"):
                        model_logger.debug(
                            f"[SIMILARITY-DEBUG][after] similarity between '{candidate.get('path', '<unknown>')}' and current: {similarity:.6f}"
                        )

                    candidate_copy = candidate.copy()
                    candidate_copy["similarity_score"] = similarity
                    scored_candidates.append(candidate_copy)

                except Exception as e:
                    if is_log_type_enabled("model"):
                        model_logger.debug(f"Ошибка вычисления схожести для {candidate['path']}: {e}")
                    # Добавляем кандидата без score
                    candidate_copy = candidate.copy()
                    candidate_copy["similarity_score"] = 0
                    scored_candidates.append(candidate_copy)

            return scored_candidates

        except Exception as e:
            logger.error(f"Ошибка при вычислении similarity scores: {e}")
            return candidates

    def _get_fused_vectors(self, rel_path: str):
        """
        Возвращает (rf_proba, yamnet_prior, fused_proba) для трека или (None, None, None).
        """
        try:
            ext = load_scan_result_extended(os.path.normpath(rel_path))
            if not ext:
                return None, None, None
            # ext: (genre, mtime, confidence, features, rf, yam, fused)
            if len(ext) >= 7:
                return ext[4], ext[5], ext[6]
            return None, None, None
        except Exception as e:
            if is_log_type_enabled("model"):
                model_logger.debug(f"[similarity] _get_fused_vectors error for {rel_path}: {e}")
            return None, None, None

    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Вычисляет косинусное сходство между двумя векторами"""
        try:
            dot_product = np.dot(a, b)
            norm_a = np.linalg.norm(a)
            norm_b = np.linalg.norm(b)

            if is_log_type_enabled("model"):
                model_logger.debug(
                    f"[SIMILARITY-DEBUG][cosine] norm_a={norm_a}, norm_b={norm_b}, возвращаю 0"
                )
            if norm_a == 0 or norm_b == 0:
                return 0

            return dot_product / (norm_a * norm_b)
        except:
            return 0


def find_similar_track(current_track_path: str, current_genre: str = None, use_model: bool = False) -> Optional[Dict]:
    """
    Функция-обертка для поиска похожего трека.
    Аргумент use_model: если True — искать всегда через модель (аудиоанализ), если False — как обычно.
    """
    finder = TrackSimilarityFinder()
    result = finder.find_similar_track(current_track_path, current_genre, use_model=use_model)

    if result:
        # Убеждаемся, что пути правильно нормализованы
        result["redirect"] = os.path.normpath(result["redirect"]).replace("\\", "/")
        result["folder"] = os.path.dirname(result["redirect"])
        result["filename"] = os.path.basename(result["redirect"])

    return result
