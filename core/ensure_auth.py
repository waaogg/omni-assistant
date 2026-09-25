#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ensure_auth.py - 确保 Windows 凭据管理器中的 gemini:antigravity 始终指向有效账号
"""

import sys
import ctypes
import json
import base64
from ctypes import wintypes
from pathlib import Path

def ensure_keyring_account(expected_email='waaoggai@gmail.com'):
    advapi32 = ctypes.WinDLL('advapi32.dll')
    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ('Flags', wintypes.DWORD),
            ('Type', wintypes.DWORD),
            ('TargetName', wintypes.LPWSTR),
            ('Comment', wintypes.LPWSTR),
            ('LastWritten', wintypes.FILETIME),
            ('CredentialBlobSize', wintypes.DWORD),
            ('CredentialBlob', ctypes.POINTER(ctypes.c_char)),
            ('Persist', wintypes.DWORD),
            ('AttributeCount', wintypes.DWORD),
            ('Attributes', ctypes.c_void_p),
            ('TargetAlias', wintypes.LPWSTR),
            ('UserName', wintypes.LPWSTR)
        ]

    pcred = ctypes.POINTER(CREDENTIAL)()
    needs_update = True
    if advapi32.CredReadW('gemini:antigravity', 1, 0, ctypes.byref(pcred)):
        try:
            blob = ctypes.string_at(pcred.contents.CredentialBlob, pcred.contents.CredentialBlobSize)
            data = json.loads(blob.decode('utf-8'))
            parts = data.get('id_token', '').split('.')
            if len(parts) > 1:
                cur_email = json.loads(base64.urlsafe_b64decode(parts[1] + '===')).get('email')
                if cur_email == expected_email:
                    needs_update = False
        except Exception:
            pass
        advapi32.CredFree(pcred)

    if needs_update:
        creds_path = Path.home() / '.gemini' / 'oauth_creds.json'
        if creds_path.exists():
            import datetime
            with open(creds_path, 'r', encoding='utf-8') as f:
                creds = json.load(f)
            exp_dt = datetime.datetime.fromtimestamp(creds['expiry_date'] / 1000.0, datetime.timezone.utc).astimezone()
            new_payload = {
                'token': {
                    'access_token': creds['access_token'],
                    'token_type': 'Bearer',
                    'refresh_token': creds['refresh_token'],
                    'expiry': exp_dt.isoformat()
                },
                'auth_method': 'consumer',
                'id_token': creds['id_token']
            }
            blob_bytes = json.dumps(new_payload).encode('utf-8')

            class CRED_WRITE(ctypes.Structure):
                _fields_ = [
                    ('Flags', wintypes.DWORD),
                    ('Type', wintypes.DWORD),
                    ('TargetName', wintypes.LPWSTR),
                    ('Comment', wintypes.LPWSTR),
                    ('LastWritten', wintypes.FILETIME),
                    ('CredentialBlobSize', wintypes.DWORD),
                    ('CredentialBlob', ctypes.c_char_p),
                    ('Persist', wintypes.DWORD),
                    ('AttributeCount', wintypes.DWORD),
                    ('Attributes', ctypes.c_void_p),
                    ('TargetAlias', wintypes.LPWSTR),
                    ('UserName', wintypes.LPWSTR)
                ]
            c = CRED_WRITE()
            c.Flags = 0
            c.Type = 1
            c.TargetName = 'gemini:antigravity'
            c.CredentialBlobSize = len(blob_bytes)
            c.CredentialBlob = blob_bytes
            c.Persist = 2
            c.UserName = 'antigravity'
            advapi32.CredWriteW(ctypes.byref(c), 0)
            print(f'UPDATED: Switched credential to {expected_email}')
            return 0
        print('ERROR: oauth_creds.json missing', file=sys.stderr)
        return 1
    print(f'OK: Credential matches {expected_email}')
    return 0

if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else 'waaoggai@gmail.com'
    sys.exit(ensure_keyring_account(target))
