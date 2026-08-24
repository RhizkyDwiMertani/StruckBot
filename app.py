import io
import re
import uuid
from datetime import date, datetime

import gspread
import pandas as pd
import streamlit as st
from google.cloud import vision
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload


# =========================================================
# APP CONFIG
# =========================================================

st.set_page_config(
    page_title="Receipt Tracker",
    page_icon="🧾",
    layout="wide",
)

SPREADSHEET_NAME = "Receipt Tracker"
WORKSHEET_NAME = "Receipts"

SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
]

VISION_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
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


# =========================================================
# SESSION STATE
# =========================================================

def init_session_state():
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


init_session_state()


# =========================================================
# GOOGLE AUTHENTICATION
# =========================================================

def get_service_account_info():
    if "google_service_account" not in st.secrets:
        raise RuntimeError(
            "Secret [google_service_account] tidak ditemukan. "
            "Tambahkan credential di Streamlit Secrets."
        )

    return dict(st.secrets["google_service_account"])


def get_credentials(scopes):
    info = get_service_account_info()

    required_fields = [
        "type",
        "project_id",
        "private_key",
        "client_email",
    ]

    missing = [
        field
        for field in required_fields
        if not info.get(field)
    ]

    if missing:
        raise RuntimeError(
            "Field credential yang belum ada: "
            + ", ".join(missing)
        )

    try:
        return Credentials.from_service_account_info(
            info,
            scopes=scopes,
        )
    except Exception as exc:
        raise RuntimeError(
            "Gagal membuat Google credentials. "
            "Periksa private_key dan isi Secrets."
        ) from exc


# =========================================================
# GOOGLE CLIENTS
# =========================================================

@st.cache_resource
def get_vision_client():
    credentials = get_credentials(VISION_SCOPES)

    return vision.ImageAnnotatorClient(
        credentials=credentials
    )


@st.cache_resource
def get_sheet_client():
    credentials = get_credentials(SHEETS_SCOPES)

    return gspread.authorize(credentials)


@st.cache_resource
def get_worksheet():
    client = get_sheet_client()

    spreadsheet = client.open(SPREADSHEET_NAME)
    worksheet = spreadsheet.worksheet(WORKSHEET_NAME)

    return worksheet


@st.cache_resource
def get_drive_service():
    credentials = get_credentials(DRIVE_SCOPES)

    return build(
        "drive",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )


# =========================================================
# GOOGLE SERVICE TEST
# =========================================================

def test_google_services():
    info = get_service_account_info()

    results = {
        "project_id": info.get("project_id", ""),
        "client_email": info.get("client_email", ""),
        "vision": False,
        "sheets": False,
        "drive": False,
    }

    get_vision_client()
    results["vision"] = True

    worksheet = get_worksheet()
    results["sheets"] = worksheet is not None

    get_drive_service()
    results["drive"] = True

    return results


# =========================================================
# GOOGLE VISION OCR
# =========================================================

def perform_ocr(image_bytes):
    client = get_vision_client()

    image = vision.Image(content=image_bytes)

    response = client.document_text_detection(
        image=image
    )

    if response.error.message:
        raise RuntimeError(
            f"Google Vision API error: {response.error.message}"
        )

    text = ""

    if response.full_text_annotation:
        text = response.full_text_annotation.text or ""

    return text.strip()


# =========================================================
# HELPERS
# =========================================================

def parse_amount(value):
    if value is None:
        return 0

    text = str(value).strip()

    if not text:
        return 0

    digits = re.sub(r"[^0-9]", "", text)

    if not digits:
        return 0

    return int(digits)


def extract_date(text):
    patterns = [
        r"\b(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})\b",
        r"\b(\d{1,2})\s+(\d{1,2})\s+(\d{2,4})\b",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text):
            try:
                day = int(match.group(1))
                month = int(match.group(2))
                year = int(match.group(3))

                if year < 100:
                    year += 2000

                return date(year, month, day)
            except ValueError:
                continue

    return date.today()


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

    for line in lines[:10]:
        lower = line.lower()

        if len(line) < 3:
            continue

        if any(word in lower for word in ignored):
            continue

        digit_count = len(re.findall(r"\d", line))

        if digit_count >= 3:
            continue

        return line

    return lines[0]


def extract_amount_by_keyword(text, keywords):
    for line in text.splitlines():
        lower = line.lower()

        if not any(keyword in lower for keyword in keywords):
            continue

        matches = re.findall(
            r"(?:rp\.?\s*)?([\d.,]+)",
            line,
            flags=re.IGNORECASE,
        )

        if matches:
            return parse_amount(matches[-1])

    return 0


def extract_items(text):
    excluded = [
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

    items = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        lower = line.lower()

        if any(word in lower for word in excluded):
            continue

        price_match = re.search(
            r"(?:rp\.?\s*)?([\d.,]+)\s*$",
            line,
            flags=re.IGNORECASE,
        )

        if not price_match:
            continue

        price = parse_amount(price_match.group(1))

        if price <= 0:
            continue

        item_name = line[:price_match.start()].strip()

        if len(item_name) < 2:
            continue

        qty = 1

        x_qty = re.search(
            r"\b[xX]\s*(\d+)\b",
            item_name,
        )

        if x_qty:
            qty = int(x_qty.group(1))
            item_name = re.sub(
                r"\b[xX]\s*\d+\b",
                "",
                item_name,
            ).strip()
        else:
            numeric_qty = re.match(
                r"^(\d+)\s+(.+)$",
                item_name,
            )

            if numeric_qty:
                possible_qty = int(numeric_qty.group(1))

                if 1 <= possible_qty <= 50:
                    qty = possible_qty
                    item_name = numeric_qty.group(2).strip()

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


def parse_receipt(text):
    subtotal = extract_amount_by_keyword(
        text,
        ["subtotal", "sub total"],
    )

    tax = extract_amount_by_keyword(
        text,
        ["tax", "pajak", "ppn"],
    )

    discount = extract_amount_by_keyword(
        text,
        ["discount", "diskon"],
    )

    total = extract_amount_by_keyword(
        text,
        ["grand total", "total", "jumlah"],
    )

    items = extract_items(text)

    if subtotal == 0:
        subtotal = sum(
            item["subtotal"]
            for item in items
        )

    if total == 0:
        total = subtotal + tax - discount

    return {
        "date": extract_date(text),
        "store": extract_store(text),
        "subtotal": subtotal,
        "tax": tax,
        "discount": discount,
        "total": total,
        "items": items,
    }


# =========================================================
# GOOGLE DRIVE
# =========================================================

def get_drive_folder_id():
    return str(
        st.secrets.get(
            "google_drive_folder_id",
            ""
        )
    ).strip()


def upload_to_drive(image_bytes, filename, mime_type):
    service = get_drive_service()

    metadata = {
        "name": filename,
    }

    folder_id = get_drive_folder_id()

    if folder_id:
        metadata["parents"] = [folder_id]

    media = MediaIoBaseUpload(
        io.BytesIO(image_bytes),
        mimetype=mime_type or "image/jpeg",
        resumable=False,
    )

    uploaded = (
        service.files()
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
        "url": file_url,
    }


# =========================================================
# GOOGLE SHEETS
# =========================================================

def ensure_sheet_header():
    worksheet = get_worksheet()

    current_headers = worksheet.row_values(1)

    if current_headers != EXPECTED_HEADERS:
        worksheet.update(
            "A1:Q1",
            [EXPECTED_HEADERS],
        )


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

    items = receipt_data.get("items", [])

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


def load_receipts():
    worksheet = get_worksheet()

    records = worksheet.get_all_records()

    if not records:
        return pd.DataFrame()

    return pd.DataFrame(records)


# =========================================================
# UI - CONNECTION TEST
# =========================================================

def show_connection_test():
    st.header("🔧 Connection Test")

    st.write(
        "Gunakan halaman ini untuk mengecek "
        "Google Vision, Google Sheets, dan Google Drive."
    )

    if st.button(
        "Test Semua Koneksi",
        type="primary",
    ):
        try:
            results = test_google_services()

            st.success("Credential berhasil dibaca.")

            st.write(
                f"**Project ID:** `{results['project_id']}`"
            )

            st.write(
                f"**Service Account:** "
                f"`{results['client_email']}`"
            )

            st.success("✅ Google Vision berhasil.")
            st.success("✅ Google Sheets berhasil.")
            st.success("✅ Google Drive berhasil.")

        except Exception as exc:
            st.error("❌ Connection test gagal.")
            st.exception(exc)

            st.info(
                "Periksa Cloud Vision API, Google Sheets API, "
                "Google Drive API, Service Account, dan Streamlit Secrets."
            )


# =========================================================
# UI - UPLOAD
# =========================================================

def show_upload_page():
    st.header("📷 Upload Receipt")

    uploaded_file = st.file_uploader(
        "Pilih foto receipt",
        type=["jpg", "jpeg", "png", "webp"],
        help=(
            "Gunakan foto yang jelas, tidak blur, "
            "dan seluruh receipt terlihat."
        ),
    )

    if uploaded_file is not None:
        image_bytes = uploaded_file.getvalue()

        st.session_state.image_bytes = image_bytes
        st.session_state.image_name = uploaded_file.name
        st.session_state.mime_type = uploaded_file.type

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
                    ocr_text = perform_ocr(image_bytes)

                if not ocr_text:
                    st.warning(
                        "OCR tidak menemukan teks pada foto."
                    )
                    return

                st.session_state.ocr_text = ocr_text
                st.session_state.receipt_data = parse_receipt(
                    ocr_text
                )
                st.session_state.ocr_completed = True

                st.success("✅ OCR berhasil.")

            except Exception as exc:
                st.error("❌ OCR gagal.")
                st.exception(exc)

                st.warning(
                    "Jika error menunjukkan 401 authentication, "
                    "buka menu Connection Test."
                )

    show_review_section()


# =========================================================
# UI - REVIEW
# =========================================================

def show_review_section():
    if not st.session_state.receipt_data:
        return

    st.divider()
    st.header("✏️ Review Hasil OCR")

    receipt = st.session_state.receipt_data

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

    st.subheader("Daftar Item")

    original_items = list(
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

    for index, item in enumerate(original_items):
        col1, col2, col3 = st.columns([4, 1, 2])

        with col1:
            item_name = st.text_input(
                f"Item {index + 1}",
                value=item["item"],
                key=f"item_{index}",
            )

        with col2:
            qty = st.number_input(
                "Qty",
                min_value=1,
                value=int(item["qty"]),
                step=1,
                key=f"qty_{index}",
            )

        with col3:
            price = st.number_input(
                "Harga",
                min_value=0,
                value=int(item["price"]),
                step=1000,
                key=f"price_{index}",
            )

        edited_items.append(
            {
                "item": item_name,
                "qty": qty,
                "price": price,
                "subtotal": qty * price,
            }
        )

    if st.button("➕ Tambah Item Manual"):
        st.session_state.receipt_data["items"].append(
            {
                "item": "",
                "qty": 1,
                "price": 0,
                "subtotal": 0,
            }
        )
        st.rerun()

    calculated_subtotal = sum(
        item["subtotal"]
        for item in edited_items
    )

    st.subheader("Nilai Transaksi")

    col1, col2, col3 = st.columns(3)

    with col1:
        subtotal = st.number_input(
            "Subtotal",
            min_value=0,
            value=int(
                receipt["subtotal"] or calculated_subtotal
            ),
            step=1000,
        )

    with col2:
        tax = st.number_input(
            "Pajak",
            min_value=0,
            value=int(receipt["tax"]),
            step=1000,
        )

    with col3:
        discount = st.number_input(
            "Diskon",
            min_value=0,
            value=int(receipt["discount"]),
            step=1000,
        )

    calculated_total = (
        subtotal + tax - discount
    )

    total = st.number_input(
        "TOTAL",
        min_value=0,
        value=int(
            receipt["total"] or calculated_total
        ),
        step=1000,
    )

    note = st.text_area(
        "Catatan"
    )

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

    with st.expander("🔎 Lihat hasil OCR mentah"):
        st.text(st.session_state.ocr_text)

    st.divider()

    if st.button(
        "💾 Simpan Receipt",
        type="primary",
        use_container_width=True,
    ):
        if not store.strip():
            st.error("Nama toko wajib diisi.")
            return

        if st.session_state.image_bytes is None:
            st.error(
                "Foto receipt tidak ditemukan. "
                "Upload kembali foto receipt."
            )
            return

        receipt_id = (
            "RC-"
            + datetime.now().strftime("%Y%m%d")
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
            with st.spinner(
                "1/2 Upload foto ke Google Drive..."
            ):
                drive_result = upload_to_drive(
                    image_bytes=st.session_state.image_bytes,
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

            with st.spinner(
                "2/2 Menyimpan data ke Google Sheets..."
            ):
                save_receipt_to_sheet(
                    receipt_data=final_receipt,
                    receipt_id=receipt_id,
                    photo_url=drive_result["url"],
                    category=category,
                    payment_method=payment_method,
                    note=note,
                    ocr_text=st.session_state.ocr_text,
                )

            st.success(
                "✅ Receipt berhasil disimpan."
            )

            st.info(
                f"Receipt ID: {receipt_id}"
            )

            st.markdown(
                f"[📷 Buka Foto di Google Drive]"
                f"({drive_result['url']})"
            )

            clear_receipt_state()

        except Exception as exc:
            st.error(
                "❌ Gagal menyimpan receipt."
            )
            st.exception(exc)


# =========================================================
# UI - HISTORY
# =========================================================

def show_history_page():
    st.header("📋 Riwayat Receipt")

    try:
        df = load_receipts()

        if df.empty:
            st.info("Belum ada receipt.")
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
                filtered["Kategori"] == category_filter
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

        available = [
            col
            for col in display_columns
            if col in filtered.columns
        ]

        st.dataframe(
            filtered[available],
            use_container_width=True,
            hide_index=True,
        )

    except Exception as exc:
        st.error(
            "❌ Gagal membaca Google Sheets."
        )
        st.exception(exc)


# =========================================================
# UI - DASHBOARD
# =========================================================

def show_dashboard_page():
    st.header("📊 Dashboard")

    try:
        df = load_receipts()

        if df.empty:
            st.info("Belum ada data receipt.")
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
            receipt_summary["Receipt_ID"]
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
            .sort_values(ascending=False)
        )

        st.subheader(
            "Pengeluaran Berdasarkan Kategori"
        )

        st.bar_chart(category_data)

    except Exception as exc:
        st.error(
            "❌ Gagal mengambil data dashboard."
        )
        st.exception(exc)


# =========================================================
# MAIN
# =========================================================

def main():
    st.title("🧾 Receipt Tracker")

    st.caption(
        "Foto Receipt → OCR → Review → "
        "Google Drive + Google Sheets"
    )

    try:
        ensure_sheet_header()
    except Exception as exc:
        st.error(
            "Tidak dapat mengakses Google Sheets."
        )
        st.exception(exc)
        st.info(
            "Periksa nama spreadsheet, worksheet, "
            "Service Account, dan Google Sheets API."
        )

    page = st.sidebar.radio(
        "Menu",
        [
            "Upload Receipt",
            "Riwayat",
            "Dashboard",
            "Connection Test",
        ],
    )

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
