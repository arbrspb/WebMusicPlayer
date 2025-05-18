import os
import xml.etree.ElementTree as ET
import urllib.parse
import json
import logging
logger = logging.getLogger(__name__)

OUTPUT_DIR = "reckordbox_parcer_file_output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def parse_reckordbox_xml(xml_path, output_path=None):
    output_path = output_path or os.path.join(OUTPUT_DIR, "parsed_reckordbox.json")
    tree = ET.parse(xml_path)
    root = tree.getroot()
    collection = root.find("COLLECTION")
    if collection is None:
        raise ValueError("COLLECTION section not found in XML")

    tracks = []
    for track in collection.findall("TRACK"):
        location_raw = track.attrib.get("Location", "")
        path = ""
        if location_raw.startswith("file://localhost/"):
            path = urllib.parse.unquote(location_raw.replace("file://localhost/", ""))
            if os.name == "nt":
                path = path.replace("/", "\\")
        else:
            path = urllib.parse.unquote(location_raw)
        genre = track.attrib.get("Genre", "")
        rating = int(track.attrib.get("Rating", "0"))
        bpm = float(track.attrib.get("AverageBpm", "0.0"))
        name = track.attrib.get("Name", "")
        artist = track.attrib.get("Artist", "")
        track_id = track.attrib.get("TrackID", "")
        tracks.append({
            "path": path,
            "genre": genre,
            "rating": rating,
            "bpm": bpm,
            "name": name,
            "artist": artist,
            "track_id": track_id
        })
    def lower_keys_track(track):
        return {k.lower(): v for k, v in track.items()}
    tracks = [lower_keys_track(track) for track in tracks]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(tracks, f, ensure_ascii=False, indent=2)
    return output_path