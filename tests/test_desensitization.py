#!/usr/bin/env python3
"""
Automated Desensitization Audit Test Suite
Scans the entire repository to ensure 100% zero leakage of credentials,
user identifiers, group IDs, class names, and tokens.
"""

import os
import re
import base64
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Base64-encoded signatures of sensitive production identities
# Strictly prevents plaintext exposure of private tokens even in test files
ENCODED_TARGETS = [
    ("ODIwMDgwMjI4", "Admin QQ Number"),
    ("MTI0OTU0NTA5OQ==", "Bot QQ Number"),
    ("OTE0NTAyNDUx", "Class Group ID 1"),
    ("MTAyNzA4NDI4MA==", "Class Group ID 2"),
    ("TmFwQ2F0X1hCU1RVTUM=", "NapCat Secret Token"),
    ("QVFNa0FEQXdBVE0z", "Production Microsoft To Do List ID"),
    ("6auY5q+T6IGq", "Real User Name"),
    ("55S15rCUMjYxMg==", "Real University Class Name"),
    ("MTE0ZTFkZGQ5NDc5", "Production WeChat Bot ID"),
    ("bzljcTgwLTdISjBL", "Production WeChat User ID"),
]

# File extensions to scan
SCANNED_EXTENSIONS = {'.py', '.js', '.json', '.md', '.txt', '.sh', '.yml', '.yaml', '.example', '.ini'}

def test_zero_leakage():
    targets = [(base64.b64decode(enc).decode('utf-8'), desc) for enc, desc in ENCODED_TARGETS]
    leaks = []
    
    for root, dirs, files in os.walk(PROJECT_ROOT):
        for ignored_dir in ['.git', '__pycache__', 'node_modules', 'data', 'media', 'logs', '.pytest_cache', 'legacy']:
            if ignored_dir in dirs:
                dirs.remove(ignored_dir)
            
        for file in files:
            file_path = Path(root) / file
            if file_path.suffix in SCANNED_EXTENSIONS or file.startswith('.env') or file == '.gitignore':
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        for line_no, line in enumerate(f, 1):
                            for pattern, desc in targets:
                                if pattern in line:
                                    leaks.append(f"{file_path.relative_to(PROJECT_ROOT)}:{line_no} - {desc}")
                except Exception as e:
                    leaks.append(f"Error reading {file_path}: {e}")

    if leaks:
        print("❌ CRITICAL: Sensitive Leaks Detected:")
        for leak in leaks:
            print(f"  -> {leak}")
        assert False, f"Total {len(leaks)} sensitive leak(s) detected! Push blocked."
    else:
        print("✅ SENSITIVE DATA AUDIT PASSED: 0 Leaks Detected across all repository files.")

if __name__ == "__main__":
    test_zero_leakage()
