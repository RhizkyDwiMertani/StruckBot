import io
import re
import uuid
from datetime import datetime, date

import pandas as pd
import streamlit as st
import gspread

from google.oauth2.service_account import Credentials
from google.cloud import vision
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


# ============================================================
# CONFIGURATION
# ============================================================

SPREADSHEET_NAME = "Receipt Tracker"
WORKSHEET_NAME = "Receipts"

GOOGLE_SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
]


# ============================================================
# GOOGLE CREDENTIALS
# ============================================================

@st.cache_resource
def get_credentials():
    """
    Read Google Service Account credentials from Streamlit Secrets.
    """

    credentials_info = dict(
        st.secrets["google_service_account"]
    )

    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=GOOGLE_SHEETS_SCOPES,
    )

    return credentials


# ============================================================
# GOOGLE SHEETS CONNECTION
# ============================================================

@st.cache_resource
def get_google_sheet():

    credentials = get_credentials()

    client = gspread.authorize(credentials)

    spreadsheet = client.open(
        SPREADSHEET_NAME
    )

    worksheet = spreadsheet.worksheet(
        WORKSHEET_NAME
    )

    return worksheet


# ============================================================
# GOOGLE DRIVE CONNECTION
# ============================================================

@st.cache_resource
def get_drive_service():

    credentials = get_credentials()

    service = build(
        "drive",
        "v3",
        credentials=credentials,
    )

    return service


# ============================================================
# OCR
# ============================================================

def perform_ocr(image_bytes):
    """
    Run Google Cloud Vision document OCR.
    """

    credentials = get_credentials()

    client = vision.ImageAnnotatorClient(
        credentials=credentials
    )

    image = vision.Image(
        content=image_bytes
    )

    response = client.document_text_detection(
        image=image
    )

    if response.error.message:

        raise RuntimeError(
            response.error.message
        )

    if not response.full_text_annotation.text:

        return ""

    return response.full_text_annotation.text.strip()


# ============================================================
# NUMBER PARSER
# ============================================================

def parse_rupiah(value):
    """
    Convert strings like:
        Rp 25.000
        25.000
        Rp25,000
        25000
    into integer.
    """

    if value is None:
        return 0

    text = str(value)

    digits = re.sub(
        r"[^0-9]",
        "",
        text
    )

    if not digits:
        return 0

    return int(digits)


# ============================================================
# DATE PARSER
# ============================================================

def extract_date(text):
    """
    Try to detect common Indonesian date formats.
    """

    patterns = [
        r"\b(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})\b",
        r"\b(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})\b",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text
        )

        if not match:
            continue

        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3))

        if year < 100:
            year += 2000

        try:

            return date(
                year,
                month,
                day
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

    ignored_keywords = [
        "invoice",
        "receipt",
        "struk",
        "tanggal",
        "date",
        "kasir",
        "cashier",
        "alamat",
        "telp",
        "phone",
    ]

    for line in lines[:8]:

        lower = line.lower()

        if len(line) < 3:
            continue

        if any(
            keyword in lower
            for keyword in ignored_keywords
        ):
            continue

        if re.search(
            r"[0-9]{3,}",
            line
        ):
            continue

        return line

    return lines[0]


# ============================================================
# TOTAL EXTRACTION
# ============================================================

def extract_amount_by_keyword(
    text,
    keywords
):

    lines = text.splitlines()

    for line in lines:

        lower = line.lower()

        if not any(
            keyword in lower
            for keyword in keywords
        ):
            continue

        numbers = re.findall(
            r"(?:rp\.?\s*)?[\d.,]+",
            line,
            flags=re.IGNORECASE
        )

        if numbers:

            return parse_rupiah(
                numbers[-1]
            )

    return 0


# ============================================================
# ITEM EXTRACTION
# ============================================================

def extract_items(text):
    """
    Heuristic receipt item extraction.

    Examples detected:
        Roti 15.000
        2 Aqua 10.000
        Indomie x2 7.000

    User reviews all detected items before saving.
    """

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
        "bayar",
        "payment",
        "tunai",
        "debit",
        "credit",
        "qris",
        "nomor",
        "invoice",
        "tanggal",
        "date",
        "kasir",
    ]

    for line in lines:

        line = line.strip()

        if not line:
            continue

        lower = line.lower()

        if any(
            keyword in lower
            for keyword in excluded_keywords
        ):
            continue

        # Find price at end of line
        price_match = re.search(
            r"(?:Rp\.?\s*)?([\d.,]+)\s*$",
            line,
            flags=re.IGNORECASE
        )

        if not price_match:
            continue

        price_text = price_match.group(1)

        price = parse_rupiah(
            price_text
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

        # Pattern: x2 / X2
        qty_match = re.search(
            r"\b[xX]\s*(\d+)\b",
            item_name
        )

        if qty_match:

            qty = int(
                qty_match.group(1)
            )

            item_name = re.sub(
                r"\b[xX]\s*\d+\b",
                "",
                item_name
            ).strip()

        else:

            # Pattern: 2 item_name
            qty_match = re.match(
                r"^(\d+)\s+(.+)$",
                item_name
            )

            if qty_match:

                possible_qty = int(
                    qty_match.group(1)
                )

                if 1 <= possible_qty <= 50:

                    qty = possible_qty

                    item_name = (
                        qty_match.group(2)
                        .strip()
                    )

        if not item_name:
            continue

        items.append({
            "item": item_name,
            "qty": qty,
            "price": price,
            "subtotal": qty * price,
        })

    return items


# ============================================================
# PARSE RECEIPT
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
        ]
    )

    tax = extract_amount_by_keyword(
        text,
        [
            "tax",
            "pajak",
            "ppn",
        ]
    )

    discount = extract_amount_by_keyword(
        text,
        [
            "discount",
            "diskon",
        ]
    )

    total = extract_amount_by_keyword(
        text,
        [
            "grand total",
            "total",
            "jumlah",
        ]
    )

    items = extract_items(
        text
    )

    # If subtotal doesn't exist,
    # calculate from items.
    if subtotal == 0:

        subtotal = sum(
            item["subtotal"]
            for item in items
        )

    # If total doesn't exist,
    # calculate.
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
# UPLOAD PHOTO TO GOOGLE DRIVE
# ============================================================

def upload_to_drive(
    image_bytes,
    filename,
    mime_type,
):
    """
    Upload receipt photo to Google Drive.

    Optional secret:
        google_drive_folder_id

    If provided, file is uploaded into that folder.
    """

    drive_service = get_drive_service()

    metadata = {
        "name": filename,
    }

    if "google_drive_folder_id" in st.secrets:

        folder_id = st.secrets[
            "google_drive_folder_id"
        ]

        if folder_id:

            metadata["parents"] = [
                folder_id
            ]

    media = MediaIoBaseUpload(
        io.BytesIO(image_bytes),
        mimetype=mime_type,
        resumable=False,
    )

    uploaded_file = (
        drive_service.files()
        .create(
            body=metadata,
            media_body=media,
            fields="id,name,webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )

    file_id = uploaded_file["id"]

    view_url = (
        f"https://drive.google.com/file/d/"
        f"{file_id}/view"
    )

    return {
        "id": file_id,
        "name": uploaded_file["name"],
        "url": view_url,
    }


# ============================================================
# SAVE TO GOOGLE SHEETS
# ============================================================

def save_to_google_sheet(
    receipt_data,
    receipt_id,
    photo_url,
    category,
    payment_method,
    note,
    ocr_text,
):
    """
    Save every item as a row.

    One receipt may generate multiple rows.
    Receipt ID identifies which rows belong together.
    """

    worksheet = get_google_sheet()

    created_at = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    rows = []

    items = receipt_data["items"]

    # If OCR finds no item,
    # still save one row.
    if not items:

        items = [{
            "item": "",
            "qty": 1,
            "price": 0,
            "subtotal": 0,
        }]

    for item in items:

        rows.append([
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
        ])

    worksheet.append_rows(
        rows,
        value_input_option="USER_ENTERED",
    )


# ============================================================
# CREATE SHEET HEADER
# ============================================================

def create_header_if_needed():

    worksheet = get_google_sheet()

    headers = worksheet.row_values(1)

    expected_headers = [
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

    if headers != expected_headers:

        worksheet.update(
            "A1:Q1",
            [expected_headers]
        )


# ============================================================
# LOAD HISTORY
# ============================================================

def load_receipts():

    worksheet = get_google_sheet()

    records = worksheet.get_all_records()

    if not records:

        return pd.DataFrame()

    return pd.DataFrame(
        records
    )


# ============================================================
# SESSION STATE
# ============================================================

if "ocr_text" not in st.session_state:
    st.session_state.ocr_text = ""

if "receipt_data" not in st.session_state:
    st.session_state.receipt_data = None

if "image_bytes" not in st.session_state:
    st.session_state.image_bytes = None

if "image_name" not in st.session_state:
    st.session_state.image_name = None

if "mime_type" not in st.session_state:
    st.session_state.mime_type = None


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="Receipt Tracker",
    page_icon="🧾",
    layout="wide",
)


# ============================================================
# APP TITLE
# ============================================================

st.title("🧾 Receipt Tracker")

st.caption(
    "Upload foto receipt → OCR → Review → Google Drive + Google Sheets"
)


# ============================================================
# CREATE HEADER
# ============================================================

try:

    create_header_if_needed()

except Exception as e:

    st.error(
        "Gagal menyiapkan Google Sheets."
    )

    st.exception(e)

    st.stop()


# ============================================================
# SIDEBAR
# ============================================================

page = st.sidebar.radio(
    "Menu",
    [
        "Upload Receipt",
        "Riwayat",
        "Dashboard",
    ],
)


# ============================================================
# UPLOAD RECEIPT PAGE
# ============================================================

if page == "Upload Receipt":

    st.header(
        "Upload Foto Receipt"
    )

    uploaded_file = st.file_uploader(
        "Upload foto receipt",
        type=[
            "jpg",
            "jpeg",
            "png",
            "webp",
        ],
        help="Gunakan foto receipt yang jelas dan tidak blur.",
    )

    if uploaded_file:

        image_bytes = uploaded_file.getvalue()

        st.image(
            image_bytes,
            caption="Receipt yang di-upload",
            use_container_width=True,
        )

        st.info(
            "Klik tombol OCR untuk membaca receipt."
        )

        if st.button(
            "🔍 Jalankan OCR",
            type="primary",
        ):

            with st.spinner(
                "Membaca receipt..."
            ):

                try:

                    text = perform_ocr(
                        image_bytes
                    )

                    if not text:

                        st.warning(
                            "OCR tidak menemukan teks."
                        )

                    else:

                        receipt_data = (
                            parse_receipt(
                                text
                            )
                        )

                        st.session_state.ocr_text = (
                            text
                        )

                        st.session_state.receipt_data = (
                            receipt_data
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

                        st.success(
                            "OCR berhasil."
                        )

                except Exception as e:

                    st.error(
                        "OCR gagal dijalankan."
                    )

                    st.exception(e)


    # ========================================================
    # OCR RESULT / REVIEW
    # ========================================================

    if st.session_state.receipt_data:

        st.divider()

        st.header(
            "Review Hasil OCR"
        )

        receipt = st.session_state.receipt_data

        # ----------------------------------------------------
        # BASIC INFO
        # ----------------------------------------------------

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


        # ----------------------------------------------------
        # ITEMS
        # ----------------------------------------------------

        st.subheader(
            "Daftar Item"
        )

        items = receipt["items"]

        if not items:

            items = [
                {
                    "item": "",
                    "qty": 1,
                    "price": 0,
                    "subtotal": 0,
                }
            ]

        edited_items = []

        for i, item in enumerate(items):

            col1, col2, col3 = st.columns(
                [4, 1, 2]
            )

            with col1:

                item_name = st.text_input(
                    f"Item {i + 1}",
                    value=item["item"],
                    key=f"item_{i}",
                )

            with col2:

                qty = st.number_input(
                    "Qty",
                    min_value=1,
                    value=int(item["qty"]),
                    step=1,
                    key=f"qty_{i}",
                )

            with col3:

                price = st.number_input(
                    "Harga",
                    min_value=0,
                    value=int(item["price"]),
                    step=1000,
                    key=f"price_{i}",
                )

            edited_items.append({
                "item": item_name,
                "qty": qty,
                "price": price,
                "subtotal": qty * price,
            })


        # ----------------------------------------------------
        # FINANCIAL REVIEW
        # ----------------------------------------------------

        calculated_subtotal = sum(
            item["subtotal"]
            for item in edited_items
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


        total_default = (
            subtotal
            + tax
            - discount
        )

        total = st.number_input(
            "TOTAL",
            min_value=0,
            value=int(
                receipt["total"]
                or total_default
            ),
            step=1000,
        )


        note = st.text_area(
            "Catatan"
        )


        # ----------------------------------------------------
        # SUMMARY
        # ----------------------------------------------------

        st.divider()

        st.subheader(
            "Summary"
        )

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


        # ----------------------------------------------------
        # OCR RAW TEXT
        # ----------------------------------------------------

        with st.expander(
            "Lihat hasil OCR mentah"
        ):

            st.text(
                st.session_state.ocr_text
            )


        # ----------------------------------------------------
        # SAVE
        # ----------------------------------------------------

        if st.button(
            "💾 Simpan Receipt",
            type="primary",
            use_container_width=True,
        ):

            if not store.strip():

                st.error(
                    "Nama toko wajib diisi."
                )

            elif not edited_items:

                st.error(
                    "Minimal ada satu item."
                )

            else:

                receipt_id = (
                    f"RC-{datetime.now().strftime('%Y%m%d')}-"
                    f"{uuid.uuid4().hex[:6].upper()}"
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

                    with st.spinner(
                        "Mengupload foto ke Google Drive..."
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


                    with st.spinner(
                        "Menyimpan data ke Google Sheets..."
                    ):

                        save_to_google_sheet(
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
                        "Receipt berhasil disimpan."
                    )

                    st.markdown(
                        f"[📷 Buka Foto Receipt di Google Drive]"
                        f"({drive_result['url']})"
                    )

                    # Reset
                    st.session_state.ocr_text = ""
                    st.session_state.receipt_data = None
                    st.session_state.image_bytes = None
                    st.session_state.image_name = None
                    st.session_state.mime_type = None


                except Exception as e:

                    st.error(
                        "Gagal menyimpan receipt."
                    )

                    st.exception(e)


# ============================================================
# HISTORY
# ============================================================

elif page == "Riwayat":

    st.header(
        "Riwayat Receipt"
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
                    "Cari toko"
                )

            with col2:

                category_filter = st.selectbox(
                    "Filter kategori",
                    [
                        "All"
                    ]
                    + sorted(
                        df["Kategori"]
                        .dropna()
                        .astype(str)
                        .unique()
                        .tolist()
                    ),
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

            display_columns = [
                column
                for column in display_columns
                if column in filtered_df.columns
            ]

            st.dataframe(
                filtered_df[display_columns],
                use_container_width=True,
                hide_index=True,
            )

    except Exception as e:

        st.error(
            "Gagal membaca Google Sheets."
        )

        st.exception(e)


# ============================================================
# DASHBOARD
# ============================================================

elif page == "Dashboard":

    st.header(
        "Dashboard"
    )

    try:

        df = load_receipts()

        if df.empty:

            st.info(
                "Belum ada data."
            )

        else:

            df["Total"] = pd.to_numeric(
                df["Total"],
                errors="coerce",
            )

            # One total per receipt
            receipt_summary = (
                df.groupby(
                    "Receipt_ID",
                    as_index=False
                )
                .first()
            )


            total_expense = (
                receipt_summary["Total"]
                .sum()
            )

            total_receipts = (
                receipt_summary[
                    "Receipt_ID"
                ].nunique()
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

            # ------------------------------------------------
            # CATEGORY
            # ------------------------------------------------

            category_data = (
                receipt_summary
                .groupby("Kategori")["Total"]
                .sum()
                .sort_values(
                    ascending=False
                )
            )

            st.subheader(
                "Pengeluaran per Kategori"
            )

            st.bar_chart(
                category_data
            )


    except Exception as e:

        st.error(
            "Gagal mengambil data dashboard."
        )

        st.exception(e)
