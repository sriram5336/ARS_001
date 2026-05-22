"""
db_backup.py  -  SmartBilling Single DB Backup & Restore
"""

import os
import sys
import shutil
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive


def get_app_dir():
    """
    Return the persistent app directory (same folder as the .exe when frozen,
    otherwise the folder containing this source file).

    This is critical: when packaged with PyInstaller (--onefile), __file__
    points to a temporary _MEIxxxx folder that gets deleted on exit, so any
    DB / credentials written there are lost. Using sys.executable's dir keeps
    everything next to the .exe so data persists across runs and across systems.
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


# ── All files live next to the .exe (or this script when running from source) ──
_BASE_DIR          = get_app_dir()
CLIENT_SECRET_FILE = os.path.join(_BASE_DIR, "client_secrets.json")
CREDS_FILE         = os.path.join(_BASE_DIR, "drive_creds.json")
DB_FILE            = os.path.join(_BASE_DIR, "smartbilling.db")
BACKUP_FOLDER_NAME = "SmartBilling_Backup"
ROOT_FOLDER_ID     = "1Yi5FgPk8Z-tVC3UmfuEKCJF8yvXIJS-0"

_drive     = None
_folder_id = None
_file_id   = None


def _get_drive():
    global _drive
    if _drive is not None:
        return _drive

    gauth = GoogleAuth()
    gauth.LoadClientConfigFile(CLIENT_SECRET_FILE)

    if os.path.exists(CREDS_FILE):
        gauth.LoadCredentialsFile(CREDS_FILE)

    if gauth.credentials is None:
        gauth.LocalWebserverAuth()
    elif gauth.access_token_expired:
        gauth.Refresh()
    else:
        gauth.Authorize()

    gauth.SaveCredentialsFile(CREDS_FILE)
    _drive = GoogleDrive(gauth)
    return _drive


def _get_backup_folder_id():
    global _folder_id
    if _folder_id:
        return _folder_id

    drive = _get_drive()
    query = (
        f"title='{BACKUP_FOLDER_NAME}' and "
        f"'{ROOT_FOLDER_ID}' in parents and trashed=false and "
        f"mimeType='application/vnd.google-apps.folder'"
    )
    results = drive.ListFile({'q': query}).GetList()
    if results:
        _folder_id = results[0]['id']
    else:
        folder = drive.CreateFile({
            'title': BACKUP_FOLDER_NAME,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [{'id': ROOT_FOLDER_ID}]
        })
        folder.Upload()
        _folder_id = folder['id']

    return _folder_id


def _get_existing_file_id():
    global _file_id
    if _file_id:
        return _file_id

    drive     = _get_drive()
    folder_id = _get_backup_folder_id()
    query = (
        f"title='smartbilling.db' and "
        f"'{folder_id}' in parents and trashed=false"
    )
    results = drive.ListFile({'q': query}).GetList()
    if results:
        _file_id = results[0]['id']

    return _file_id


def backup_db_to_drive():
    try:
        if not os.path.exists(DB_FILE):
            return

        drive     = _get_drive()
        folder_id = _get_backup_folder_id()
        file_id   = _get_existing_file_id()

        if file_id:
            f = drive.CreateFile({'id': file_id})
        else:
            f = drive.CreateFile({
                'title': 'smartbilling.db',
                'parents': [{'id': folder_id}]
            })

        f.SetContentFile(DB_FILE)
        f.Upload()

        global _file_id
        _file_id = f['id']

        print("[Backup] smartbilling.db updated on Google Drive")

    except Exception as e:
        print(f"[Backup] Failed: {e}")


def restore_db_from_drive():
    try:
        drive     = _get_drive()
        folder_id = _get_backup_folder_id()

        query = (
            f"title='smartbilling.db' and "
            f"'{folder_id}' in parents and trashed=false"
        )
        results = drive.ListFile({'q': query}).GetList()

        if not results:
            print("[Restore] No backup found. Starting fresh.")
            return False

        if os.path.exists(DB_FILE):
            shutil.copy2(DB_FILE, DB_FILE + ".old")

        f = drive.CreateFile({'id': results[0]['id']})
        f.GetContentFile(DB_FILE)

        print("[Restore] smartbilling.db restored from Google Drive!")
        return True

    except Exception as e:
        print(f"[Restore] Failed: {e}")
        return False
