import streamlit as st
import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from datetime import date
import uuid


# =========================================================
# CONFIGURATION
# =========================================================

SPREADSHEET_NAME = "Receipt Tracker"
WORKSHEET_NAME = "Receipts"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]


# =========================================================
# CONNECT TO GOOGLE SHEETS
# =========================================================

@st.cache_resource
def connect_to_google_sheet():

    credentials = Credentials.from_service_account_info(
        st.secrets["google_service_account"],
        scopes=SCOPES
    )

    client = gspread.authorize(credentials)

    spreadsheet = client.open(SPREADSHEET_NAME)

    worksheet = spreadsheet.worksheet(WORKSHEET_NAME)

    return worksheet


# =========================================================
# INSERT RECEIPT
# =========================================================

def save_receipt(
    worksheet,
    transaction_date,
    store,
    category,
    items,
    tax,
    discount,
    payment,
    note
):

    receipt_id = str(uuid.uuid4())[:8]

    rows = []

    subtotal_total = 0

    for item in items:

        item_name = item["item"]
        qty = item["qty"]
        price = item["price"]

        subtotal = qty * price

        subtotal_total += subtotal

        rows.append([
            receipt_id,
            transaction_date,
            store,
            category,
            item_name,
            qty,
            price,
            subtotal,
            tax,
            discount,
            0,  # Temporary total
            payment,
            note
        ])

    total = subtotal_total + tax - discount

    # Update total for all rows belonging to this receipt
    for row in rows:
        row[10] = total

    worksheet.append_rows(rows, value_input_option="USER_ENTERED")

    return total


# =========================================================
# LOAD DATA
# =========================================================

def load_receipts(worksheet):

    data = worksheet.get_all_records()

    if not data:
        return pd.DataFrame()

    return pd.DataFrame(data)


# =========================================================
# STREAMLIT UI
# =========================================================

st.set_page_config(
    page_title="Receipt Tracker",
    page_icon="🧾",
    layout="wide"
)

st.title("🧾 Receipt Tracker")

st.caption(
    "Catat transaksi dan simpan otomatis ke Google Spreadsheet."
)


# =========================================================
# SIDEBAR
# =========================================================

st.sidebar.header("Menu")

menu = st.sidebar.radio(
    "Pilih halaman",
    [
        "Tambah Receipt",
        "Riwayat Receipt",
        "Dashboard"
    ]
)


# =========================================================
# ADD RECEIPT
# =========================================================

if menu == "Tambah Receipt":

    st.header("Tambah Receipt")

    try:
        worksheet = connect_to_google_sheet()
    except Exception as e:
        st.error("Gagal terhubung ke Google Spreadsheet.")
        st.exception(e)
        st.stop()

    col1, col2 = st.columns(2)

    with col1:

        transaction_date = st.date_input(
            "Tanggal",
            value=date.today()
        )

        store = st.text_input(
            "Nama Toko / Vendor"
        )

        category = st.selectbox(
            "Kategori",
            [
                "Food",
                "Transportation",
                "Shopping",
                "Bills",
                "Entertainment",
                "Health",
                "Other"
            ]
        )

    with col2:

        payment = st.selectbox(
            "Metode Pembayaran",
            [
                "Cash",
                "Debit",
                "Credit Card",
                "E-Wallet",
                "Bank Transfer"
            ]
        )

        tax = st.number_input(
            "Pajak",
            min_value=0,
            value=0,
            step=1000
        )

        discount = st.number_input(
            "Diskon",
            min_value=0,
            value=0,
            step=1000
        )

    st.divider()

    st.subheader("Daftar Barang")

    if "items" not in st.session_state:
        st.session_state.items = []

    col1, col2, col3 = st.columns([3, 1, 2])

    with col1:
        item_name = st.text_input(
            "Nama Item",
            key="item_name"
        )

    with col2:
        qty = st.number_input(
            "Qty",
            min_value=1,
            value=1,
            step=1,
            key="qty"
        )

    with col3:
        price = st.number_input(
            "Harga",
            min_value=0,
            value=0,
            step=1000,
            key="price"
        )

    if st.button("➕ Tambahkan Item"):

        if not item_name:
            st.warning("Nama item harus diisi.")

        elif price <= 0:
            st.warning("Harga harus lebih dari 0.")

        else:

            st.session_state.items.append({
                "item": item_name,
                "qty": qty,
                "price": price
            })

            st.success("Item berhasil ditambahkan.")


    # =====================================================
    # DISPLAY ITEMS
    # =====================================================

    if st.session_state.items:

        st.subheader("Item yang Ditambahkan")

        subtotal = 0

        table_data = []

        for i, item in enumerate(st.session_state.items):

            item_subtotal = (
                item["qty"] * item["price"]
            )

            subtotal += item_subtotal

            table_data.append({
                "Item": item["item"],
                "Qty": item["qty"],
                "Harga": item["price"],
                "Subtotal": item_subtotal
            })

        df_items = pd.DataFrame(table_data)

        st.dataframe(
            df_items,
            use_container_width=True,
            hide_index=True
        )

        total = subtotal + tax - discount

        st.metric(
            "TOTAL",
            f"Rp {total:,.0f}"
        )

        note = st.text_area(
            "Catatan"
        )

        if st.button(
            "💾 Simpan Receipt",
            type="primary"
        ):

            if not store:
                st.warning(
                    "Nama toko harus diisi."
                )

            else:

                try:

                    total_saved = save_receipt(
                        worksheet=worksheet,
                        transaction_date=str(
                            transaction_date
                        ),
                        store=store,
                        category=category,
                        items=st.session_state.items,
                        tax=tax,
                        discount=discount,
                        payment=payment,
                        note=note
                    )

                    st.success(
                        f"Receipt berhasil disimpan. "
                        f"Total: Rp {total_saved:,.0f}"
                    )

                    # Reset items
                    st.session_state.items = []

                except Exception as e:

                    st.error(
                        "Gagal menyimpan receipt."
                    )

                    st.exception(e)


# =========================================================
# RECEIPT HISTORY
# =========================================================

elif menu == "Riwayat Receipt":

    st.header("Riwayat Receipt")

    try:
        worksheet = connect_to_google_sheet()

        df = load_receipts(worksheet)

        if df.empty:

            st.info(
                "Belum ada receipt."
            )

        else:

            search = st.text_input(
                "🔍 Cari toko"
            )

            if search:

                df = df[
                    df["Toko"]
                    .astype(str)
                    .str.contains(
                        search,
                        case=False,
                        na=False
                    )
                ]

            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True
            )

    except Exception as e:

        st.error(
            "Gagal membaca Google Spreadsheet."
        )

        st.exception(e)


# =========================================================
# DASHBOARD
# =========================================================

elif menu == "Dashboard":

    st.header("Dashboard")

    try:

        worksheet = connect_to_google_sheet()

        df = load_receipts(worksheet)

        if df.empty:

            st.info(
                "Belum ada data untuk ditampilkan."
            )

        else:

            # Convert total to numeric
            df["Total"] = pd.to_numeric(
                df["Total"],
                errors="coerce"
            )

            df["Qty"] = pd.to_numeric(
                df["Qty"],
                errors="coerce"
            )

            # Metrics
            total_expense = (
                df["Total"]
                .drop_duplicates()
                .sum()
            )

            total_transactions = (
                df["ID"]
                .nunique()
            )

            col1, col2 = st.columns(2)

            with col1:

                st.metric(
                    "Total Pengeluaran",
                    f"Rp {total_expense:,.0f}"
                )

            with col2:

                st.metric(
                    "Jumlah Transaksi",
                    total_transactions
                )

            st.divider()

            # Expense by category
            category_expense = (
                df.groupby("Kategori")["Total"]
                .first()
                .sort_values(
                    ascending=False
                )
            )

            st.subheader(
                "Pengeluaran Berdasarkan Kategori"
            )

            st.bar_chart(
                category_expense
            )

    except Exception as e:

        st.error(
            "Gagal mengambil data dashboard."
        )

        st.exception(e)
