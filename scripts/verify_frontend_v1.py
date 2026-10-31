import glob
import os
import re


def check_frontend_v1():
    print("=== Checking frontend/ File Integrity ===")
    root = "frontend"
    js_files = sorted(glob.glob(os.path.join(root, "js", "*.js")) + [os.path.join(root, "app.js")])
    print(f"Found {len(js_files)} JS files in frontend/")

    errors = 0
    # Check imports
    for js_path in js_files:
        with open(js_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Find imports: import ... from './...'
        import_matches = re.findall(r'from\s+[\'"]([^\'"]+)[\'"]', content)
        for imp in import_matches:
            if imp.startswith("."):
                resolved = os.path.normpath(os.path.join(os.path.dirname(js_path), imp))
                if not os.path.exists(resolved):
                    print(f"  [BROKEN IMPORT] in {js_path}: '{imp}' (resolved: {resolved})")
                    errors += 1

    # Check index.html DOM IDs
    with open(os.path.join(root, "index.html"), "r", encoding="utf-8") as f:
        html_content = f.read()
    html_ids = set(re.findall(r'id=["\']([^"\']+)["\']', html_content))

    with open(os.path.join(root, "js", "state.js"), "r", encoding="utf-8") as f:
        state_content = f.read()
    queried_ids = re.findall(r'document\.getElementById\(["\']([^"\']+)["\']\)', state_content)

    missing_ids = [i for i in queried_ids if i not in html_ids]
    if missing_ids:
        print(f"  [MISSING DOM IDs] {len(missing_ids)} IDs in state.js not in index.html:")
        for m in missing_ids:
            print(f"    - {m}")
        errors += len(missing_ids)
    else:
        print(f"  [DOM IDs] All {len(queried_ids)} queried IDs in state.js are present in index.html.")

    # Check CSS file
    css_path = os.path.join(root, "styles.css")
    if os.path.exists(css_path):
        with open(css_path, "r", encoding="utf-8") as f:
            css_text = f.read()
        opens = css_text.count("{")
        closes = css_text.count("}")
        print(f"  [CSS] styles.css: {len(css_text.splitlines())} lines, open braces: {opens}, close braces: {closes}")
        if opens != closes:
            print("  [CSS MISMATCH] Braces do not balance!")
            errors += 1
    else:
        print("  [CSS MISSING] styles.css not found!")
        errors += 1

    print(f"\nFrontend checks completed with {errors} errors.")
    return errors

if __name__ == "__main__":
    check_frontend_v1()
