from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from datetime import datetime
import os
import sys


def get_app_dir():
    """Same logic as db_backup.get_app_dir() — see that file for rationale."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


# ── All files live next to the .exe (or this script when running from source) ──
_BASE_DIR          = get_app_dir()
CLIENT_SECRET_FILE = os.path.join(_BASE_DIR, "client_secrets.json")
CREDS_FILE         = os.path.join(_BASE_DIR, "drive_creds.json")
ROOT_FOLDER_ID     = "1Yi5FgPk8Z-tVC3UmfuEKCJF8yvXIJS-0"

_drive = None
folder_cache = {}

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


def get_or_create_folder(folder_name, parent_id):
    cache_key = f"{parent_id}_{folder_name}"
    if cache_key in folder_cache:
        return folder_cache[cache_key]

    drive = _get_drive()
    query = (
        f"title='{folder_name}' and "
        f"'{parent_id}' in parents and trashed=false and "
        f"mimeType='application/vnd.google-apps.folder'"
    )
    folder_list = drive.ListFile({'q': query}).GetList()

    if folder_list:
        folder_id = folder_list[0]['id']
    else:
        folder = drive.CreateFile({
            'title': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [{'id': parent_id}]
        })
        folder.Upload()
        folder_id = folder['id']
        print(f"[Drive] Created Folder: {folder_name}")

    folder_cache[cache_key] = folder_id
    return folder_id


def upload_pdf_to_drive(pdf_path, invoice_no, invoice_date=None):
    try:
        drive = _get_drive()

        if not os.path.exists(pdf_path):
            print(f"PDF not found: {pdf_path}")
            return None

        dt = datetime.strptime(invoice_date, "%Y-%m-%d") if invoice_date else datetime.now()

        year_folder  = dt.strftime("%Y")
        month_folder = dt.strftime("%m-%B")
        date_folder  = dt.strftime("%d-%m-%Y")

        year_id  = get_or_create_folder(year_folder,  ROOT_FOLDER_ID)
        month_id = get_or_create_folder(month_folder, year_id)
        date_id  = get_or_create_folder(date_folder,  month_id)

        file_name = f"{invoice_no}.pdf"
        file = drive.CreateFile({
            'title': file_name,
            'parents': [{'id': date_id}]
        })
        file.SetContentFile(pdf_path)
        file.Upload()

        file.InsertPermission({'type': 'anyone', 'value': 'anyone', 'role': 'reader'})

        link = f"https://drive.google.com/file/d/{file['id']}/view"
        print("\n[Drive] Upload Successful:", link)
        return link

    except Exception as e:
        print("[Drive] Upload Failed:", e)
        return None


def upload_monthly_report_to_drive(pdf_path, year, month):
    try:
        drive = _get_drive()

        if not os.path.exists(pdf_path):
            print(f"Monthly report PDF not found: {pdf_path}")
            return None

        year_folder  = str(year)
        month_folder = datetime(year, month, 1).strftime("%m-%B")

        year_id    = get_or_create_folder(year_folder,    ROOT_FOLDER_ID)
        month_id   = get_or_create_folder(month_folder,   year_id)
        reports_id = get_or_create_folder("Monthly Reports", month_id)

        file_name = os.path.basename(pdf_path)
        file = drive.CreateFile({
            'title': file_name,
            'parents': [{'id': reports_id}]
        })
        file.SetContentFile(pdf_path)
        file.Upload()

        file.InsertPermission({'type': 'anyone', 'value': 'anyone', 'role': 'reader'})

        link = f"https://drive.google.com/file/d/{file['id']}/view"
        print("[Drive] Monthly report uploaded:", link)
        return link

    except Exception as e:
        print("[Drive] Monthly report upload failed:", e)
        return None
