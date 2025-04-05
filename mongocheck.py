import os
import fnmatch

def print_dir_structure(path='.'):
    print("Directory structure:")
    print("-------------------")
    
    for root, dirs, files in os.walk(path):
        level = root.replace(path, '').count(os.sep)
        indent = ' ' * 4 * level
        print(f"{indent}{os.path.basename(root)}/")
        
        sub_indent = ' ' * 4 * (level + 1)
        for file in sorted(files):
            if not file.startswith('.env') and not fnmatch.fnmatch(file, '*token*'):
                size = os.path.getsize(os.path.join(root, file))
                print(f"{sub_indent}{file} ({size//1024}KB)")

print_dir_structure()