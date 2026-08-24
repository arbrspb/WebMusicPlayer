def check_html_tags(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.split('\n')
    open_divs = []

    for i, line in enumerate(lines, 1):
        # Найти открывающие div
        if '<div' in line and not line.strip().startswith('<!--'):
            open_divs.append((i, line.strip()))

        # Найти закрывающие div
        if '</div>' in line and not line.strip().startswith('<!--'):
            if open_divs:
                open_divs.pop()

    if open_divs:
        print("Незакрытые div теги:")
        for line_num, line_content in open_divs:
            print(f"Строка {line_num}: {line_content}")
    else:
        print("Все div теги закрыты корректно")


if __name__ == "__main__":
    check_html_tags("main.html")