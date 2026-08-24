import io
import re
import uuid
from datetime import date, datetime

import pandas as pd
import streamlit as st
import gspread

from google.oauth2.service_account import Credentials
from google.cloud import vision
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="Receipt Tracker",
    page_icon="🧾",
    layout="wide",
)


# ============================================================
# CONFIGURATION
# ============================================================

SPREADSHEET_NAME = "Receipt Tracker"
WORKSHEET_NAME = "Receipts"

# Google Sheets
SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

# Google Drive
DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
]

# Google Cloud Vision
VISION_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
]


# ============================================================
# SESSION STATE
# ============================================================

DEFAULT_SESSION_STATE = {
    "ocr_text": "",
    "receipt_data": None,
    "image_bytes": None,
    "image_name": None,
    "mime_type": None,
    "ocr_completed": False,
}

for key, value in DEFAULT_SESSION_STATE.items():

    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# GOOGLE CREDENTIALS
# ============================================================

def load_service_account_info():
    """
    Read Google Service Account information
    from Streamlit Secrets.
    """

    if "google_service_account" not in st.secrets:

        raise RuntimeError(
            "Secret [google_service_account] tidak ditemukan. "
            "Pastikan secrets.toml telah dikonfigurasi."
        )

    return dict(
        st.secrets["google_service_account"]
    )


def create_credentials(scopes):
    """
    Create Google credentials with specific scopes.
    """

    service_account_info = (
        load_service_account_info()
    )

    credentials = (
        Credentials.from_service_account_info(
            service_account_info,
            scopes=scopes,
        )
    )

    return credentials


# ============================================================
# GOOGLE CLOUD VISION
# ============================================================

@st.cache_resource
def get_vision_client():

    credentials = create_credentials(
        VISION_SCOPES
    )

    client = vision.ImageAnnotatorClient(
        credentials=credentials
    )

    return client


# ============================================================
# GOOGLE SHEETS
# ============================================================

@st.cache_resource
def get_sheet_client():

    credentials = create_credentials(
        SHEETS_SCOPES
    )

    client = gspread.authorize(
        credentials
    )

    return client


@st.cache_resource
def get_worksheet():

    client = get_sheet_client()

    spreadsheet = client.open(
        SPREADSHEET_NAME
    )

    worksheet = spreadsheet.worksheet(
        WORKSHEET_NAME
    )

    return worksheet


# ============================================================
# GOOGLE DRIVE
# ============================================================

@st.cache_resource
def get_drive_service():

    credentials = create_credentials(
        DRIVE_SCOPES
    )

    service = build(
        "drive",
        "v3",
        credentials=credentials,
    )

    return service


# ============================================================
# TEST GOOGLE SERVICES
# ============================================================

def test_vision_credentials():
    """
    Returns the service account email and project ID
    without exposing private credentials.
    """

    info = load_service_account_info()

    required = [
        "project_id",
        "client_email",
        "private_key",
    ]

    missing = [
        key
        for key in required
        if not info.get(key)
    ]

    if missing:

        raise RuntimeError(
            "Field credential berikut belum ada: "
            + ", ".join(missing)
        )

    # Create credentials to verify key format.
    credentials = create_credentials(
        VISION_SCOPES
    )

    if not credentials.valid:
        raise RuntimeError(
            "Credential Google tidak valid."
        )

    return {
        "project_id": info["project_id"],
        "client_email": info["client_email"],
    }


# ============================================================
# OCR
# ============================================================

def perform_ocr(image_bytes):
    """
    Perform Google Cloud Vision OCR.
    """

    client = get_vision_client()

    image = vision.Image(
        content=image_bytes
    )

    response = client.document_text_detection(
        image=image
    )

    # API-level error
    if response.error.message:

        raise RuntimeError(
            f"Google Vision API error: "
            f"{response.error.message}"
        )

    # No OCR text
    if (
        not response.full_text_annotation
        or not response.full_text_annotation.text
    ):

        return ""

    return (
        response.full_text_annotation.text
        .strip()
    )


# ============================================================
# NUMBER PARSER
# ============================================================

def parse_amount(value):
    """
    Convert:
        Rp 25.000
        25.000
        Rp25,000
        25,000
        25000

    into integer.
    """

    if value is None:
        return 0

    text = str(value).strip()

    if not text:
        return 0

    digits = re.sub(
        r"[^0-9]",
        "",
        text,
    )

    if not digits:
        return 0

    return int(digits)


# ============================================================
# DATE EXTRACTION
# ============================================================

def extract_date(text):

    patterns = [

        # 24/08/2026
        r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b",

        # 24 08 2026
        r"\b(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})\b",
    ]

    for pattern in patterns:

        matches = re.finditer(
            pattern,
            text,
        )

        for match in matches:

            try:

                day = int(
                    match.group(1)
                )

                month = int(
                    match.group(2)
                )

                year = int(
                    match.group(3)
                )

                if year < 100:
                    year += 2000

                return date(
                    year,
                    month,
                    day,
                )

            except ValueError:

                continue

    return date.today()


# ============================================================
# STORE EXTRACTION
# ============================================================

def extract_store(text):

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    if not lines:
        return ""

    ignored = [
        "receipt",
        "struk",
        "invoice",
        "tanggal",
        "date",
        "kasir",
        "cashier",
        "alamat",
        "address",
        "telp",
        "phone",
    ]

    # Usually merchant name appears
    # near the top of a receipt.
    for line in lines[:10]:

        lower = line.lower()

        if len(line) < 3:
            continue

        if any(
            word in lower
            for word in ignored
        ):
            continue

        # Ignore lines dominated by numbers
        number_count = len(
            re.findall(
                r"\d",
                line
            )
        )

        if number_count >= 3:
            continue

        return line

    return lines[0]


# ============================================================
# AMOUNT EXTRACTION FROM KEYWORD
# ============================================================

def extract_amount_by_keyword(
    text,
    keywords,
):

    for line in text.splitlines():

        lower = line.lower()

        if not any(
            keyword in lower
            for keyword in keywords
        ):
            continue

        # Find amounts on line
        matches = re.findall(
            r"(?:rp\.?\s*)?([\d.,]+)",
            line,
            flags=re.IGNORECASE,
        )

        if not matches:
            continue

        # Last numeric value is normally amount.
        return parse_amount(
            matches[-1]
        )

    return 0


# ============================================================
# ITEM EXTRACTION
# ============================================================

def extract_items(text):

    lines = text.splitlines()

    items = []

    excluded_keywords = [
        "total",
        "subtotal",
        "sub total",
        "grand total",
        "tax",
        "pajak",
        "ppn",
        "discount",
        "diskon",
        "cash",
        "change",
        "kembali",
        "payment",
        "bayar",
        "tunai",
        "debit",
        "credit",
        "qris",
        "invoice",
        "receipt",
        "struk",
        "tanggal",
        "date",
        "kasir",
        "cashier",
        "terima kasih",
        "thank",
    ]

    for raw_line in lines:

        line = raw_line.strip()

        if not line:
            continue

        lower = line.lower()

        # Skip obvious non-item lines.
        if any(
            keyword in lower
            for keyword in excluded_keywords
        ):
            continue

        # Price at the end of line.
        price_match = re.search(
            r"(?:rp\.?\s*)?([\d.,]+)\s*$",
            line,
            flags=re.IGNORECASE,
        )

        if not price_match:
            continue

        price = parse_amount(
            price_match.group(1)
        )

        if price <= 0:
            continue

        item_name = (
            line[:price_match.start()]
            .strip()
        )

        if len(item_name) < 2:
            continue

        qty = 1

        # Example:
        # Roti x2 15000
        x_qty_match = re.search(
            r"\b[xX]\s*(\d+)\b",
            item_name,
        )

        if x_qty_match:

            qty = int(
                x_qty_match.group(1)
            )

            item_name = re.sub(
                r"\b[xX]\s*\d+\b",
                "",
                item_name,
            ).strip()

        else:

            # Example:
            # 2 Roti 15000
            numeric_qty_match = re.match(
                r"^(\d+)\s+(.+)$",
                item_name,
            )

            if numeric_qty_match:

                possible_qty = int(
                    numeric_qty_match.group(1)
                )

                if 1 <= possible_qty <= 50:

                    qty = possible_qty

                    item_name = (
                        numeric_qty_match.group(2)
                        .strip()
                    )

        if not item_name:
            continue

        items.append(
            {
                "item": item_name,
                "qty": qty,
                "price": price,
                "subtotal": qty * price,
            }
        )

    return items


# ============================================================
# PARSE OCR RESULT
# ============================================================

def parse_receipt(text):

    transaction_date = extract_date(
        text
    )

    store = extract_store(
        text
    )

    subtotal = extract_amount_by_keyword(
        text,
        [
            "subtotal",
            "sub total",
        ],
    )

    tax = extract_amount_by_keyword(
        text,
        [
            "tax",
            "pajak",
            "ppn",
        ],
    )

    discount = extract_amount_by_keyword(
        text,
        [
            "discount",
            "diskon",
        ],
    )

    total = extract_amount_by_keyword(
        text,
        [
            "grand total",
            "total",
            "jumlah",
        ],
    )

    items = extract_items(
        text
    )

    # Calculate subtotal if OCR did not find one.
    if subtotal == 0:

        subtotal = sum(
            item["subtotal"]
            for item in items
        )

    # Calculate total if OCR did not find one.
    if total == 0:

        total = (
            subtotal
            + tax
            - discount
        )

    return {
        "date": transaction_date,
        "store": store,
        "subtotal": subtotal,
        "tax": tax,
        "discount": discount,
        "total": total,
        "items": items,
    }


# ============================================================
# GOOGLE DRIVE FOLDER
# ============================================================

def get_drive_folder_id():

    folder_id = st.secrets.get(
        "google_drive_folder_id",
        "",
    )

    if not folder_id:
        return None

    return str(folder_id).strip()


# ============================================================
# UPLOAD PHOTO TO GOOGLE DRIVE
# ============================================================

def upload_to_drive(
    image_bytes,
    filename,
    mime_type,
):

    drive_service = (
        get_drive_service()
    )

    metadata = {
        "name": filename,
    }

    folder_id = (
        get_drive_folder_id()
    )

    if folder_id:

        metadata["parents"] = [
            folder_id
        ]

    media = MediaIoBaseUpload(
        io.BytesIO(image_bytes),
        mimetype=mime_type,
        resumable=False,
    )

    uploaded = (
        drive_service.files()
        .create(
            body=metadata,
            media_body=media,
            fields="id,name,mimeType,webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )

    file_id = uploaded["id"]

    file_url = (
        f"https://drive.google.com/file/d/"
        f"{file_id}/view"
    )

    return {
        "id": file_id,
        "name": uploaded["name"],
        "mime_type": uploaded["mimeType"],
        "url": file_url,
    }


# ============================================================
# GOOGLE SHEETS HEADER
# ============================================================

EXPECTED_HEADERS = [
    "Receipt_ID",
    "Tanggal",
    "Toko",
    "Kategori",
    "Item",
    "Qty",
    "Harga",
    "Subtotal_Item",
    "Subtotal_Receipt",
    "Pajak",
    "Diskon",
    "Total",
    "Metode_Pembayaran",
    "Catatan",
    "Foto_URL",
    "Created_At",
    "OCR_Text",
]


def ensure_sheet_header():

    worksheet = get_worksheet()

    current_headers = (
        worksheet.row_values(1)
    )

    if current_headers != EXPECTED_HEADERS:

        worksheet.update(
            "A1:Q1",
            [EXPECTED_HEADERS],
        )


# ============================================================
# SAVE RECEIPT TO GOOGLE SHEETS
# ============================================================

def save_receipt_to_sheet(
    receipt_data,
    receipt_id,
    photo_url,
    category,
    payment_method,
    note,
    ocr_text,
):

    worksheet = get_worksheet()

    created_at = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    items = receipt_data["items"]

    # Keep receipt even if OCR did not detect
    # item-level information.
    if not items:

        items = [
            {
                "item": "",
                "qty": 1,
                "price": 0,
                "subtotal": 0,
            }
        ]

    rows = []

    for item in items:

        rows.append(
            [
                receipt_id,
                str(receipt_data["date"]),
                receipt_data["store"],
                category,
                item["item"],
                item["qty"],
                item["price"],
                item["subtotal"],
                receipt_data["subtotal"],
                receipt_data["tax"],
                receipt_data["discount"],
                receipt_data["total"],
                payment_method,
                note,
                photo_url,
                created_at,
                ocr_text,
            ]
        )

    worksheet.append_rows(
        rows,
        value_input_option="USER_ENTERED",
    )


# ============================================================
# LOAD RECEIPTS
# ============================================================

def load_receipts():

    worksheet = get_worksheet()

    records = (
        worksheet.get_all_records()
    )

    if not records:
        return pd.DataFrame()

    return pd.DataFrame(
        records
    )


# ============================================================
# CLEAR CURRENT RECEIPT
# ============================================================

def clear_current_receipt():

    st.session_state.ocr_text = ""
    st.session_state.receipt_data = None
    st.session_state.image_bytes = None
    st.session_state.image_name = None
    st.session_state.mime_type = None
    st.session_state.ocr_completed = False


# ============================================================
# APP TITLE
# ============================================================

st.title("🧾 Receipt Tracker")

st.caption(
    "Foto Receipt → OCR → Review → Google Drive + Google Sheets"
)


# ============================================================
# SIDEBAR
# ============================================================

page = st.sidebar.radio(
    "Menu",
    [
        "Upload Receipt",
        "Riwayat",
        "Dashboard",
        "Connection Test",
    ],
)


# ============================================================
# CONNECTION TEST PAGE
# ============================================================

if page == "Connection Test":

    st.header(
        "🔧 Google Connection Test"
    )

    st.write(
        "Gunakan halaman ini untuk memastikan "
        "credential Google sudah benar."
    )

    if st.button(
        "Test Credentials"
    ):

        # -----------------------------------------------
        # Vision credentials
        # -----------------------------------------------

        try:

            info = test_vision_credentials()

            st.success(
                "✅ Credential Google valid."
            )

            st.write(
                f"**Project ID:** "
                f"`{info['project_id']}`"
            )

            st.write(
                f"**Service Account:** "
                f"`{info['client_email']}`"
            )

        except Exception as e:

            st.error(
                "❌ Credential test gagal."
            )

            st.exception(e)


        # -----------------------------------------------
        # Vision client
        # -----------------------------------------------

        try:

            get_vision_client()

            st.success(
                "✅ Google Vision client berhasil dibuat."
            )

        except Exception as e:

            st.error(
                "❌ Google Vision client gagal dibuat."
            )

            st.exception(e)


        # -----------------------------------------------
        # Sheets
        # -----------------------------------------------

        try:

            worksheet = get_worksheet()

            st.success(
                "✅ Google Sheets berhasil diakses."
            )

            st.write(
                f"Worksheet: `{worksheet.title}`"
            )

        except Exception as e:

            st.error(
                "❌ Google Sheets gagal diakses."
            )

            st.exception(e)


        # -----------------------------------------------
        # Drive
        # -----------------------------------------------

        try:

            get_drive_service()

            st.success(
                "✅ Google Drive service berhasil dibuat."
            )

        except Exception as e:

            st.error(
                "❌ Google Drive gagal diakses."
            )

            st.exception(e)


# ============================================================
# UPLOAD RECEIPT
# ============================================================

elif page == "Upload Receipt":

    st.header(
        "📷 Upload Receipt"
    )

    uploaded_file = st.file_uploader(
        "Pilih foto receipt",
        type=[
            "jpg",
            "jpeg",
            "png",
            "webp",
        ],
        help=(
            "Gunakan foto yang jelas, tidak blur, "
            "dan seluruh receipt terlihat."
        ),
    )

    if uploaded_file:

        image_bytes = (
            uploaded_file.getvalue()
        )

        # Save uploaded image to session.
        st.session_state.image_bytes = (
            image_bytes
        )

        st.session_state.image_name = (
            uploaded_file.name
        )

        st.session_state.mime_type = (
            uploaded_file.type
        )

        st.image(
            image_bytes,
            caption="Receipt",
            use_container_width=True,
        )

        st.divider()

        if st.button(
            "🔍 Jalankan OCR",
            type="primary",
            use_container_width=True,
        ):

            try:

                with st.spinner(
                    "Google Vision sedang membaca receipt..."
                ):

                    ocr_text = perform_ocr(
                        image_bytes
                    )

                if not ocr_text:

                    st.warning(
                        "Tidak ada teks yang berhasil dibaca."
                    )

                    st.session_state.ocr_completed = (
                        False
                    )

                else:

                    parsed_data = parse_receipt(
                        ocr_text
                    )

                    st.session_state.ocr_text = (
                        ocr_text
                    )

                    st.session_state.receipt_data = (
                        parsed_data
                    )

                    st.session_state.ocr_completed = (
                        True
                    )

                    st.success(
                        "✅ OCR berhasil."
                    )

            except Exception as e:

                st.error(
                    "❌ OCR gagal."
                )

                st.exception(e)

                st.info(
                    "Jika error menunjukkan 401 "
                    "'invalid authentication credentials', "
                    "buka menu Connection Test untuk diagnosis."
                )


    # ========================================================
    # REVIEW RECEIPT
    # ========================================================

    if st.session_state.receipt_data:

        st.divider()

        st.header(
            "✏️ Review Hasil OCR"
        )

        receipt = (
            st.session_state.receipt_data
        )

        # -----------------------------------------------
        # STORE & DATE
        # -----------------------------------------------

        col1, col2 = st.columns(2)

        with col1:

            store = st.text_input(
                "Nama Toko",
                value=receipt["store"],
                key="review_store",
            )

        with col2:

            transaction_date = st.date_input(
                "Tanggal",
                value=receipt["date"],
                key="review_date",
            )


        # -----------------------------------------------
        # CATEGORY & PAYMENT
        # -----------------------------------------------

        col1, col2 = st.columns(2)

        with col1:

            category = st.selectbox(
                "Kategori",
                [
                    "Food",
                    "Transportation",
                    "Shopping",
                    "Bills",
                    "Entertainment",
                    "Health",
                    "Education",
                    "Other",
                ],
                key="review_category",
            )

        with col2:

            payment_method = st.selectbox(
                "Metode Pembayaran",
                [
                    "Cash",
                    "Debit",
                    "Credit Card",
                    "E-Wallet",
                    "Bank Transfer",
                    "QRIS",
                    "Other",
                ],
                key="review_payment",
            )


        # -----------------------------------------------
        # ITEMS
        # -----------------------------------------------

        st.subheader(
            "Daftar Item"
        )

        original_items = (
            receipt.get("items", [])
        )

        if not original_items:

            original_items = [
                {
                    "item": "",
                    "qty": 1,
                    "price": 0,
                    "subtotal": 0,
                }
            ]

        edited_items = []

        for index, item in enumerate(
            original_items
        ):

            col1, col2, col3 = st.columns(
                [4, 1, 2]
            )

            with col1:

                item_name = st.text_input(
                    f"Item {index + 1}",
                    value=item["item"],
                    key=f"review_item_{index}",
                )

            with col2:

                qty = st.number_input(
                    "Qty",
                    min_value=1,
                    value=int(
                        item["qty"]
                    ),
                    step=1,
                    key=f"review_qty_{index}",
                )

            with col3:

                price = st.number_input(
                    "Harga",
                    min_value=0,
                    value=int(
                        item["price"]
                    ),
                    step=1000,
                    key=f"review_price_{index}",
                )

            edited_items.append(
                {
                    "item": item_name,
                    "qty": qty,
                    "price": price,
                    "subtotal": qty * price,
                }
            )


        # -----------------------------------------------
        # ADD EXTRA ITEM
        # -----------------------------------------------

        if st.button(
            "➕ Tambah Item Manual"
        ):

            current_items = (
                st.session_state.receipt_data[
                    "items"
                ]
            )

            current_items.append(
                {
                    "item": "",
                    "qty": 1,
                    "price": 0,
                    "subtotal": 0,
                }
            )

            st.rerun()


        # -----------------------------------------------
        # AMOUNTS
        # -----------------------------------------------

        calculated_subtotal = sum(
            item["subtotal"]
            for item in edited_items
        )

        st.subheader(
            "Nilai Transaksi"
        )

        col1, col2, col3 = st.columns(3)

        with col1:

            subtotal = st.number_input(
                "Subtotal",
                min_value=0,
                value=int(
                    receipt["subtotal"]
                    or calculated_subtotal
                ),
                step=1000,
                key="review_subtotal",
            )

        with col2:

            tax = st.number_input(
                "Pajak",
                min_value=0,
                value=int(
                    receipt["tax"]
                ),
                step=1000,
                key="review_tax",
            )

        with col3:

            discount = st.number_input(
                "Diskon",
                min_value=0,
                value=int(
                    receipt["discount"]
                ),
                step=1000,
                key="review_discount",
            )


        calculated_total = (
            subtotal
            + tax
            - discount
        )

        total = st.number_input(
            "TOTAL",
            min_value=0,
            value=int(
                receipt["total"]
                or calculated_total
            ),
            step=1000,
            key="review_total",
        )


        note = st.text_area(
            "Catatan",
            key="review_note",
        )


        # -----------------------------------------------
        # SUMMARY
        # -----------------------------------------------

        st.divider()

        col1, col2, col3 = st.columns(3)

        with col1:

            st.metric(
                "Subtotal",
                f"Rp {subtotal:,.0f}",
            )

        with col2:

            st.metric(
                "Pajak",
                f"Rp {tax:,.0f}",
            )

        with col3:

            st.metric(
                "Total",
                f"Rp {total:,.0f}",
            )


        # -----------------------------------------------
        # RAW OCR
        # -----------------------------------------------

        with st.expander(
            "🔎 Lihat hasil OCR mentah"
        ):

            st.text(
                st.session_state.ocr_text
            )


        # -----------------------------------------------
        # SAVE BUTTON
        # -----------------------------------------------

        st.divider()

        if st.button(
            "💾 Simpan Receipt",
            type="primary",
            use_container_width=True,
        ):

            if not store.strip():

                st.error(
                    "Nama toko wajib diisi."
                )

                st.stop()

            receipt_id = (
                "RC-"
                + datetime.now().strftime(
                    "%Y%m%d"
                )
                + "-"
                + uuid.uuid4().hex[:6].upper()
            )

            final_receipt = {
                "date": transaction_date,
                "store": store.strip(),
                "subtotal": subtotal,
                "tax": tax,
                "discount": discount,
                "total": total,
                "items": edited_items,
            }

            try:

                # ========================================
                # 1. UPLOAD PHOTO
                # ========================================

                with st.spinner(
                    "1/2 Upload foto ke Google Drive..."
                ):

                    drive_result = upload_to_drive(
                        image_bytes=(
                            st.session_state.image_bytes
                        ),
                        filename=(
                            receipt_id
                            + "_"
                            + st.session_state.image_name
                        ),
                        mime_type=(
                            st.session_state.mime_type
                        ),
                    )


                # ========================================
                # 2. SAVE DATA
                # ========================================

                with st.spinner(
                    "2/2 Menyimpan data ke Google Sheets..."
                ):

                    save_receipt_to_sheet(
                        receipt_data=(
                            final_receipt
                        ),
                        receipt_id=receipt_id,
                        photo_url=(
                            drive_result["url"]
                        ),
                        category=category,
                        payment_method=(
                            payment_method
                        ),
                        note=note,
                        ocr_text=(
                            st.session_state.ocr_text
                        ),
                    )


                st.success(
                    "✅ Receipt berhasil disimpan!"
                )

                st.info(
                    f"Receipt ID: {receipt_id}"
                )

                st.markdown(
                    f"[📷 Buka Foto Receipt di Google Drive]"
                    f"({drive_result['url']})"
                )


                # Reset after successful save.
                clear_current_receipt()


            except Exception as e:

                st.error(
                    "❌ Gagal menyimpan receipt."
                )

                st.exception(e)


# ============================================================
# HISTORY
# ============================================================

elif page == "Riwayat":

    st.header(
        "📋 Riwayat Receipt"
    )

    try:

        df = load_receipts()

        if df.empty:

            st.info(
                "Belum ada receipt."
            )

        else:

            col1, col2 = st.columns(2)

            with col1:

                search = st.text_input(
                    "🔍 Cari toko"
                )

            with col2:

                categories = sorted(
                    df["Kategori"]
                    .dropna()
                    .astype(str)
                    .unique()
                    .tolist()
                )

                category_filter = st.selectbox(
                    "Kategori",
                    ["All"] + categories,
                )


            filtered = df.copy()

            if search:

                filtered = filtered[
                    filtered["Toko"]
                    .astype(str)
                    .str.contains(
                        search,
                        case=False,
                        na=False,
                    )
                ]

            if category_filter != "All":

                filtered = filtered[
                    filtered["Kategori"]
                    == category_filter
                ]


            display_columns = [
                "Receipt_ID",
                "Tanggal",
                "Toko",
                "Kategori",
                "Item",
                "Qty",
                "Harga",
                "Subtotal_Item",
                "Total",
                "Metode_Pembayaran",
                "Foto_URL",
            ]

            available_columns = [
                column
                for column in display_columns
                if column in filtered.columns
            ]

            st.dataframe(
                filtered[
                    available_columns
                ],
                use_container_width=True,
                hide_index=True,
            )

    except Exception as e:

        st.error(
            "❌ Gagal membaca Google Sheets."
        )

        st.exception(e)


# ============================================================
# DASHBOARD
# ============================================================

elif page == "Dashboard":

    st.header(
        "📊 Dashboard"
    )

    try:

        df = load_receipts()

        if df.empty:

            st.info(
                "Belum ada data receipt."
            )

        else:

            df["Total"] = pd.to_numeric(
                df["Total"],
                errors="coerce",
            )

            # Keep only one row per receipt
            # for receipt-level calculations.
            receipt_summary = (
                df.groupby(
                    "Receipt_ID",
                    as_index=False,
                )
                .first()
            )

            total_expense = (
                receipt_summary["Total"]
                .fillna(0)
                .sum()
            )

            total_receipts = (
                receipt_summary[
                    "Receipt_ID"
                ]
                .nunique()
            )


            # -------------------------------------------
            # METRICS
            # -------------------------------------------

            col1, col2 = st.columns(2)

            with col1:

                st.metric(
                    "Total Pengeluaran",
                    f"Rp {total_expense:,.0f}",
                )

            with col2:

                st.metric(
                    "Jumlah Receipt",
                    total_receipts,
                )


            st.divider()


            # -------------------------------------------
            # CATEGORY CHART
            # -------------------------------------------

            category_data = (
                receipt_summary
                .groupby("Kategori")["Total"]
                .sum()
                .sort_values(
                    ascending=False
                )
            )

            st.subheader(
                "Pengeluaran Berdasarkan Kategori"
            )

            st.bar_chart(
                category_data
            )
