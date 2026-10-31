import re


def check():
    with open("frontend-v2/js/state.js", "r", encoding="utf-8") as f:
        js_text = f.read()
    with open("frontend-v2/index.html", "r", encoding="utf-8") as f:
        html_text = f.read()

    requested_ids = re.findall(r"document\.getElementById\('([^']+)'\)", js_text)
    html_ids = set(re.findall(r'id="([^"]+)"', html_text))

    missing = [i for i in requested_ids if i not in html_ids]
    print(f"Total element IDs requested by state.js: {len(requested_ids)}")
    print(f"Total element IDs defined in index.html: {len(html_ids)}")
    print(f"Missing element IDs count: {len(missing)}")
    if missing:
        for m in missing:
            print(f"  MISSING ID: {m}")
    else:
        print("PERFECT: 100% of DOM IDs referenced in state.js exist in index.html!")


if __name__ == "__main__":
    check()
