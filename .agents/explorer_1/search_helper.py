import os

keywords = ['shift', 'shiftmanager', 'shift_manager', 'shift-manager']
project_root = r"c:\Users\ADMIN\AEGIS-COGNITION"

for root, dirs, files in os.walk(os.path.join(project_root, 'core')):
    # In-place modify dirs to prevent descending into unwanted directories
    dirs[:] = [d for d in dirs if d not in ('target', 'artifacts', '__pycache__')]
    
    for file in files:
        if file.endswith(('.rs', '.py')):
            path = os.path.join(root, file)
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    for idx, line in enumerate(f, 1):
                        line_lower = line.lower()
                        if any(w in line_lower for w in keywords):
                            rel_path = os.path.relpath(path, project_root)
                            print(f"{rel_path}:{idx}: {line.strip()}")
            except Exception as e:
                pass
