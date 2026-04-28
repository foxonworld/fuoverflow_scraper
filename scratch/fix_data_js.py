import os

path = r"d:\ex\fuoverflow_scraper\data.js"
try:
    with open(path, "rb") as f:
        content = f.read()
    
    # Trim trailing whitespace/newlines from bytes
    content = content.rstrip()
    
    if content.endswith(b";;"):
        print("Found double semicolon, fixing...")
        new_content = content[:-1] # Remove one semicolon
        with open(path, "wb") as f:
            f.write(new_content)
        print("Success: Fixed trailing semicolon.")
    else:
        print("No double semicolon found at the end of the file.")
        # Let's check if it starts correctly too
        if not content.startswith(b"quizData ="):
             print("Warning: File does not start with 'quizData ='")

except Exception as e:
    print(f"Error: {e}")
