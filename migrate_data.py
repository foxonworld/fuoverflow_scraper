import json
import os
import re

def get_quiz_data():
    DATA_FILE = "data.js"
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        content = f.read().strip()
        if "quizData =" in content:
            json_str = content.split("quizData =", 1)[1].strip()
            if json_str.endswith(";"):
                json_str = json_str[:-1]
            return json.loads(json_str)
    return {}

def save_quiz_data(data):
    with open("data.js", "w", encoding="utf-8") as f:
        f.write(f"const quizData = {json.dumps(data, ensure_ascii=False)};")

def migrate():
    print("Starting Data Migration...")
    
    # 1. Migrate config.json
    if os.path.exists("config.json"):
        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        
        if "SOURCES" not in cfg:
            sources = []
            subject = cfg.get("SUBJECT_NAME", "MLN122")
            urls = cfg.get("POST_URLS", [])
            for url in urls:
                # Extra ID from url: https://fuoverflow.com/threads/mln122-sp26-c1fe.6344/ -> mln122-sp26-c1fe.6344
                match = re.search(r"threads/([^/]+)", url)
                source_id = match.group(1) if match else "unknown"
                sources.append({
                    "id": source_id,
                    "name": source_id,
                    "url": url,
                    "subject": subject,
                    "enabled": True
                })
            cfg["SOURCES"] = sources
            with open("config.json", "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=4, ensure_ascii=False)
            print("config.json migrated.")

    # 2. Migrate data.js
    data = get_quiz_data()
    modified = False
    for subject, questions in data.items():
        if subject == "_metadata": continue
        for q in questions:
            if "source_id" not in q:
                # Guess source from image filename
                # images/mln122_sp26_c1fe_6344_q001.webp -> mln122_sp26_c1fe_6344
                img = q.get("img", "")
                if img:
                    fname = os.path.basename(img)
                    match = re.match(r"(.*)_q\d+", fname)
                    if match:
                        # Convert mln122_sp26_c1fe_6344 to mln122-sp26-c1fe.6344 (best effort)
                        source_id = match.group(1).replace("_", "-")
                        # The last part is often the thread ID
                        parts = source_id.rsplit("-", 1)
                        if len(parts) > 1 and parts[1].isdigit():
                            source_id = f"{parts[0]}.{parts[1]}"
                        q["source_id"] = source_id
                        modified = True
    
    if modified:
        save_quiz_data(data)
        print("data.js migrated with source_ids.")
    else:
        print("data.js already migrated or no images found.")

if __name__ == "__main__":
    migrate()
