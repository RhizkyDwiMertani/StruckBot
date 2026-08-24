import io
import re
import uuid
from datetime import date, datetime

import gspread
import pandas as pd
import pytesseract
import streamlit as st

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from google.oauth2.service_account import Credentials
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

WORKSHEET_NAME = "Receipts"

SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
]


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


# ============================================================
# SESSION STATE
# ============================================================

def initialize_session_state():

    defaults = {
        "ocr_text": "",
        "receipt_data": None,
        "image_bytes": None,
        "image_name": None,
        "mime_type": None,
        "ocr_completed": False,
    }

    for key, value in defaults.items():

        if key not in st.session_state:
            st.session_state[key] = value


def clear_receipt_state():

    st.session_state.ocr_text = ""
    st.session_state.receipt_data = None
    st.session_state.image_bytes = None
    st.session_state.image_name = None
    st.session_state.mime_type = None
    st.session_state.ocr_completed = False


initialize_session_state()


# ============================================================
# GOOGLE CREDENTIALS
# ============================================================

def get_service_account_info():

    if "google_service_account" not in st.secrets:

        raise RuntimeError(
            "Secret [google_service_account] tidak ditemukan."
        )

    return dict(
        st.secrets["google_service_account"]
    )


def get_credentials(scopes):

    info = get_service_account_info()

    required_fields = [
        "type",
        "project_id",
        "private_key",
        "client_email",
    ]

    missing_fields = [
        field
        for field in required_fields
        if not info.get(field)
    ]

    if missing_fields:

        raise RuntimeError(
            "Credential Google belum lengkap: "
            + ", ".join(missing_fields)
        )

    try:

        return Credentials.from_service_account_info(
            info,
            scopes=scopes,
        )

    except Exception as exc:

        raise RuntimeError(
            "Gagal membuat Google credentials. "
            "Periksa konfigurasi Streamlit Secrets."
        ) from exc


# ============================================================
# GOOGLE SPREADSHEET ID
# ============================================================

def get_spreadsheet_id():

    spreadsheet_id = st.secrets.get(
        "google_spreadsheet_id",
        "",
    )

    spreadsheet_id = str(
        spreadsheet_id
    ).strip()

    if not spreadsheet_id:

        raise RuntimeError(
            "google_spreadsheet_id belum diisi."
        )

    return spreadsheet_id


# ============================================================
# GOOGLE DRIVE FOLDER
# ============================================================

def get_drive_folder_id():

    folder_id = st.secrets.get(
        "google_drive_folder_id",
        "",
    )

    return str(
        folder_id
    ).strip()


# ============================================================
# GOOGLE SHEETS
# ============================================================

@st.cache_resource
def get_sheet_client():

    credentials = get_credentials(
        SHEETS_SCOPES
    )

    return gspread.authorize(
        credentials
    )


@st.cache_resource
def get_worksheet():

    client = get_sheet_client()

    spreadsheet_id = get_spreadsheet_id()

    try:

        spreadsheet = client.open_by_key(
            spreadsheet_id
        )

    except Exception as exc:

        raise RuntimeError(
            "Tidak dapat membuka Google Spreadsheet. "
            "Periksa Spreadsheet ID dan permission Service Account."
        ) from exc

    try:

        worksheet = spreadsheet.worksheet(
            WORKSHEET_NAME
        )

    except Exception as exc:

        raise RuntimeError(
            f"Worksheet '{WORKSHEET_NAME}' tidak ditemukan."
        ) from exc

    return worksheet


# ============================================================
# GOOGLE DRIVE
# ============================================================

@st.cache_resource
def get_drive_service():

    credentials = get_credentials(
        DRIVE_SCOPES
    )

    return build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )


# ============================================================
# TESSERACT LANGUAGE
# ============================================================

@st.cache_resource
def get_ocr_language():

    try:

        languages = pytesseract.get_languages(
            config=""
        )

    except Exception:

        languages = []

    if (
        "ind" in languages
        and "eng" in languages
    ):

        return "ind+eng"

    if "ind" in languages:

        return "ind"

    return "eng"


# ============================================================
# IMAGE PREPROCESSING
# ============================================================

def preprocess_image(image):

    # Convert to RGB
    image = image.convert("RGB")

    # Upscale
    width, height = image.size

    scale = 2

    image = image.resize(
        (
            width * scale,
            height * scale,
        )
    )

    # Convert grayscale
    image = ImageOps.grayscale(
        image
    )

    # Improve contrast
    image = ImageEnhance.Contrast(
        image
    ).enhance(2.0)

    # Improve sharpness
    image = ImageEnhance.Sharpness(
        image
    ).enhance(2.0)

    # Slight denoise
    image = image.filter(
        ImageFilter.MedianFilter(
            size=3
        )
    )

    # Threshold
    image = image.point(
        lambda pixel: 255
        if pixel > 160
        else 0
    )

    return image


# ============================================================
# TESSERACT OCR
# ============================================================

def perform_ocr(image_bytes):

    try:

        image = Image.open(
            io.BytesIO(image_bytes)
        )

    except Exception as exc:

        raise RuntimeError(
            "File gambar tidak dapat dibuka."
        ) from exc

    processed_image = preprocess_image(
        image
    )

    language = get_ocr_language()

    config = (
        "--oem 3 "
        "--psm 6"
    )

    try:

        text = pytesseract.image_to_string(
            processed_image,
            lang=language,
            config=config,
        )

    except pytesseract.TesseractNotFoundError as exc:

        raise RuntimeError(
            "Tesseract OCR tidak ditemukan. "
            "Pastikan packages.txt berisi tesseract-ocr."
        ) from exc

    except Exception as exc:

        raise RuntimeError(
            "Tesseract OCR gagal dijalankan."
        ) from exc

    return text.strip()


# ============================================================
# NUMBER PARSER
# ============================================================

def parse_amount(value):

    if value is None:
        return 0

    text = str(value).strip()

    if not text:
        return 0

    text = (
        text
        .replace("Rp", "")
        .replace("rp", "")
        .replace(" ", "")
    )

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
        r"\b(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{2,4})\b",
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

    ignored_words = [
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
        "nomor",
        "no.",
    ]

    for line in lines[:10]:

        lower = line.lower()

        if len(line) < 3:
            continue

        if any(
            word in lower
            for word in ignored_words
        ):
            continue

        digit_count = len(
            re.findall(
                r"\d",
                line,
            )
        )

        if digit_count >= 3:
            continue

        return line

    return lines[0]


# ============================================================
# AMOUNT EXTRACTION
# ============================================================

def extract_amount_by_keyword(
    text,
    keywords,
):

    lines = text.splitlines()

    for line in lines:

        lower = line.lower()

        if not any(
            keyword in lower
            for keyword in keywords
        ):
            continue

        matches = re.findall(
            r"(?:rp\.?\s*)?([\d.,]+)",
            line,
            flags=re.IGNORECASE,
        )

        if matches:

            return parse_amount(
                matches[-1]
            )

    return 0


# ============================================================
# ITEM EXTRACTION
# ============================================================

def extract_items(text):

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
        "nomor",
        "telp",
        "phone",
    ]

    items = []

    for raw_line in text.splitlines():

        line = raw_line.strip()

        if not line:
            continue

        lower = line.lower()

        if any(
            keyword in lower
            for keyword in excluded_keywords
        ):
            continue

        # Remove leading bullet/symbol
        line = re.sub(
            r"^[•*\-]+\s*",
            "",
            line,
        )

        # Find price at end of line
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
            line[
                :price_match.start()
            ]
            .strip()
        )

        if len(item_name) < 2:
            continue

        # Ignore lines that are mostly numeric
        if len(
            re.findall(
                r"\d",
                item_name,
            )
        ) > len(item_name) * 0.5:

            continue

        quantity = 1

        # Example:
        # Roti x2 15000
        x_quantity = re.search(
            r"\b[xX]\s*(\d+)\b",
            item_name,
        )

        if x_quantity:

            quantity = int(
                x_quantity.group(1)
            )

            item_name = re.sub(
                r"\b[xX]\s*\d+\b",
                "",
                item_name,
            ).strip()

        else:

            # Example:
            # 2 Roti 15000
            numeric_quantity = re.match(
                r"^(\d+)\s+(.+)$",
                item_name,
            )

            if numeric_quantity:

                possible_quantity = int(
                    numeric_quantity.group(1)
                )

                if (
                    1
                    <= possible_quantity
                    <= 50
                ):

                    quantity = (
                        possible_quantity
                    )

                    item_name = (
                        numeric_quantity.group(2)
                        .strip()
                    )

        if not item_name:
            continue

        items.append(
            {
                "item": item_name,
                "qty": quantity,
                "price": price,
                "subtotal": (
                    quantity * price
                ),
            }
        )

    return items


# ============================================================
# PARSE RECEIPT
# ============================================================

def parse_receipt(text):

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
            "amount due",
        ],
    )

    items = extract_items(
        text
    )

    # Calculate subtotal from items
    if subtotal == 0:

        subtotal = sum(
            item["subtotal"]
            for item in items
        )

    # Calculate total if OCR
    # did not find total
    if total == 0:

        total = (
            subtotal
            + tax
            - discount
        )

    return {
        "date": extract_date(text),
        "store": extract_store(text),
        "subtotal": subtotal,
        "tax": tax,
        "discount": discount,
        "total": total,
        "items": items,
    }


# ============================================================
# GOOGLE DRIVE UPLOAD
# ============================================================

def upload_to_drive(
    image_bytes,
    filename,
    mime_type,
):

    service = get_drive_service()

    metadata = {
        "name": filename,
    }

    folder_id = get_drive_folder_id()

    if folder_id:

        metadata["parents"] = [
            folder_id
        ]

    media = MediaIoBaseUpload(
        io.BytesIO(image_bytes),
        mimetype=(
            mime_type
            or "image/jpeg"
        ),
        resumable=False,
    )

    uploaded_file = (
        service.files()
        .create(
            body=metadata,
            media_body=media,
            fields=(
                "id,"
                "name,"
                "mimeType,"
                "webViewLink"
            ),
            supportsAllDrives=True,
        )
        .execute()
    )

    file_id = uploaded_file["id"]

    file_url = (
        "https://drive.google.com/file/d/"
        f"{file_id}/view"
    )

    return {
        "id": file_id,
        "name": uploaded_file["name"],
        "url": file_url,
    }


# ============================================================
# GOOGLE SHEETS HEADER
# ============================================================

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
# SAVE RECEIPT TO SHEETS
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

    items = receipt_data.get(
        "items",
        [],
    )

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
                str(
                    receipt_data["date"]
                ),
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
# CONNECTION TEST
# ============================================================

def test_google_connections():

    info = get_service_account_info()

    results = {
        "project_id": info.get(
            "project_id",
            "",
        ),
        "client_email": info.get(
            "client_email",
            "",
        ),
        "sheets": False,
        "drive": False,
        "tesseract": False,
    }

    # Google Sheets
    worksheet = get_worksheet()

    if worksheet:

        results["sheets"] = True

    # Google Drive
    get_drive_service()

    results["drive"] = True

    # Tesseract
    languages = pytesseract.get_languages(
        config=""
    )

    if languages:

        results["tesseract"] = True

    results["ocr_languages"] = languages

    return results


# ============================================================
# CONNECTION TEST PAGE
# ============================================================

def show_connection_test():

    st.header(
        "🔧 Connection Test"
    )

    st.write(
        "Halaman ini digunakan untuk memastikan "
        "Google Sheets, Google Drive, dan Tesseract "
        "sudah berjalan."
    )

    if st.button(
        "Test Semua Koneksi",
        type="primary",
    ):

        try:

            results = (
                test_google_connections()
            )

            st.success(
                "Credential berhasil dibaca."
            )

            st.write(
                f"**Project ID:** "
                f"`{results['project_id']}`"
            )

            st.write(
                f"**Service Account:** "
                f"`{results['client_email']}`"
            )

            if results["sheets"]:

                st.success(
                    "✅ Google Sheets berhasil."
                )

            if results["drive"]:

                st.success(
                    "✅ Google Drive berhasil."
                )

            if results["tesseract"]:

                st.success(
                    "✅ Tesseract OCR berhasil."
                )

                languages = results[
                    "ocr_languages"
                ]

                st.write(
                    "**OCR Languages:** "
                    + ", ".join(languages)
                )

        except Exception as exc:

            st.error(
                "❌ Connection test gagal."
            )

            st.exception(exc)


# ============================================================
# UPLOAD RECEIPT PAGE
# ============================================================

def show_upload_page():

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
            "Gunakan foto receipt yang jelas "
            "dan tidak blur."
        ),
    )

    if uploaded_file is not None:

        image_bytes = (
            uploaded_file.getvalue()
        )

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
                    "Tesseract sedang membaca receipt..."
                ):

                    ocr_text = perform_ocr(
                        image_bytes
                    )

                if not ocr_text:

                    st.warning(
                        "Tesseract tidak menemukan teks "
                        "pada foto."
                    )

                    st.session_state.ocr_completed = (
                        False
                    )

                else:

                    st.session_state.ocr_text = (
                        ocr_text
                    )

                    st.session_state.receipt_data = (
                        parse_receipt(
                            ocr_text
                        )
                    )

                    st.session_state.ocr_completed = (
                        True
                    )

                    st.success(
                        "✅ OCR berhasil."
                    )

            except Exception as exc:

                st.error(
                    "❌ OCR gagal."
                )

                st.exception(exc)

                st.info(
                    "Pastikan Tesseract sudah terinstall "
                    "melalui packages.txt."
                )

    show_review_section()


# ============================================================
# REVIEW SECTION
# ============================================================

def show_review_section():

    receipt = (
        st.session_state.receipt_data
    )

    if receipt is None:

        return

    st.divider()

    st.header(
        "✏️ Review Hasil OCR"
    )

    # ========================================================
    # STORE + DATE
    # ========================================================

    col1, col2 = st.columns(2)

    with col1:

        store = st.text_input(
            "Nama Toko",
            value=receipt["store"],
        )

    with col2:

        transaction_date = st.date_input(
            "Tanggal",
            value=receipt["date"],
        )

    # ========================================================
    # CATEGORY + PAYMENT
    # ========================================================

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
        )

    # ========================================================
    # ITEMS
    # ========================================================

    st.subheader(
        "Daftar Item"
    )

    original_items = list(
        receipt.get(
            "items",
            [],
        )
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
                key=f"item_{index}",
            )

        with col2:

            quantity = st.number_input(
                "Qty",
                min_value=1,
                value=int(
                    item["qty"]
                ),
                step=1,
                key=f"qty_{index}",
            )

        with col3:

            price = st.number_input(
                "Harga",
                min_value=0,
                value=int(
                    item["price"]
                ),
                step=1000,
                key=f"price_{index}",
            )

        edited_items.append(
            {
                "item": item_name,
                "qty": quantity,
                "price": price,
                "subtotal": (
                    quantity * price
                ),
            }
        )

    # ========================================================
    # ADD MANUAL ITEM
    # ========================================================

    if st.button(
        "➕ Tambah Item Manual"
    ):

        st.session_state.receipt_data[
            "items"
        ].append(
            {
                "item": "",
                "qty": 1,
                "price": 0,
                "subtotal": 0,
            }
        )

        st.rerun()

    # ========================================================
    # AMOUNTS
    # ========================================================

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
        )

    with col2:

        tax = st.number_input(
            "Pajak",
            min_value=0,
            value=int(
                receipt["tax"]
            ),
            step=1000,
        )

    with col3:

        discount = st.number_input(
            "Diskon",
            min_value=0,
            value=int(
                receipt["discount"]
            ),
            step=1000,
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
    )

    note = st.text_area(
        "Catatan"
    )

    # ========================================================
    # SUMMARY
    # ========================================================

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

    # ========================================================
    # OCR RAW TEXT
    # ========================================================

    with st.expander(
        "🔎 Lihat hasil OCR mentah"
    ):

        st.text(
            st.session_state.ocr_text
        )

    # ========================================================
    # SAVE
    # ========================================================

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

            return

        if (
            st.session_state.image_bytes
            is None
        ):

            st.error(
                "Foto receipt tidak ditemukan."
            )

            return

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

            # ------------------------------------------------
            # Upload photo
            # ------------------------------------------------

            with st.spinner(
                "1/2 Upload foto ke Google Drive..."
            ):

                drive_result = (
                    upload_to_drive(
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
                            or "image/jpeg"
                        ),
                    )
                )

            # ------------------------------------------------
            # Save data
            # ------------------------------------------------

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
                "✅ Receipt berhasil disimpan."
            )

            st.info(
                f"Receipt ID: {receipt_id}"
            )

            st.markdown(
                "[📷 Buka foto receipt di Google Drive]"
                f"({drive_result['url']})"
            )

            clear_receipt_state()

        except Exception as exc:

            st.error(
                "❌ Gagal menyimpan receipt."
            )

            st.exception(exc)


# ============================================================
# HISTORY PAGE
# ============================================================

def show_history_page():

    st.header(
        "📋 Riwayat Receipt"
    )

    try:

        df = load_receipts()

        if df.empty:

            st.info(
                "Belum ada receipt."
            )

            return

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

        filtered_df = df.copy()

        if search:

            filtered_df = filtered_df[
                filtered_df["Toko"]
                .astype(str)
                .str.contains(
                    search,
                    case=False,
                    na=False,
                )
            ]

        if category_filter != "All":

            filtered_df = filtered_df[
                filtered_df["Kategori"]
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
            if column in filtered_df.columns
        ]

        st.dataframe(
            filtered_df[
                available_columns
            ],
            use_container_width=True,
            hide_index=True,
        )

    except Exception as exc:

        st.error(
            "❌ Gagal membaca Google Sheets."
        )

        st.exception(exc)


# ============================================================
# DASHBOARD
# ============================================================

def show_dashboard_page():

    st.header(
        "📊 Dashboard"
    )

    try:

        df = load_receipts()

        if df.empty:

            st.info(
                "Belum ada data receipt."
            )

            return

        df["Total"] = pd.to_numeric(
            df["Total"],
            errors="coerce",
        )

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

    except Exception as exc:

        st.error(
            "❌ Gagal mengambil data dashboard."
        )

        st.exception(exc)


# ============================================================
# MAIN
# ============================================================

def main():

    st.title(
        "🧾 Receipt Tracker"
    )

    st.caption(
        "Foto Receipt → Tesseract OCR → Review → "
        "Google Drive + Google Sheets"
    )

    # --------------------------------------------------------
    # Prepare Sheets
    # --------------------------------------------------------

    try:

        ensure_sheet_header()

    except Exception as exc:

        st.error(
            "Tidak dapat mengakses Google Sheets."
        )

        st.exception(exc)

        st.info(
            "Periksa Spreadsheet ID, "
            "Service Account, permission Editor, "
            "dan Google Sheets API."
        )

    # --------------------------------------------------------
    # Sidebar
    # --------------------------------------------------------

    page = st.sidebar.radio(
        "Menu",
        [
            "Upload Receipt",
            "Riwayat",
            "Dashboard",
            "Connection Test",
        ],
    )

    # --------------------------------------------------------
    # Pages
    # --------------------------------------------------------

    if page == "Upload Receipt":

        show_upload_page()

    elif page == "Riwayat":

        show_history_page()

    elif page == "Dashboard":

        show_dashboard_page()

    elif page == "Connection Test":

        show_connection_test()


if __name__ == "__main__":
    main()
