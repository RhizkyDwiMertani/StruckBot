import streamlit as st
import gspread
import pandas as pd

from google.oauth2.service_account import Credentials
from datetime import date
import uuid


# ============================================================
# CONFIG
# ============================================================

SPREADSHEET_NAME = "Receipt Tracker"
WORKSHEET_NAME = "Receipts"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# ============================================================
# GOOGLE SHEETS CONNECTION
# ============================================================

@st.cache_resource
def connect_google_sheet():

    credentials = Credentials.from_service_account_info(
        st.secrets["google_service_account"],
        scopes=SCOPES,
    )

    client = gspread.authorize(credentials)

    spreadsheet = client.open(SPREADSHEET_NAME)

    worksheet = spreadsheet.worksheet(WORKSHEET_NAME)

    return worksheet


# ============================================================
# LOAD DATA
# ============================================================

def load_data(worksheet):

    records = worksheet.get_all_records()

    if not records:
        return pd.DataFrame()

    return pd.DataFrame(records)


# ============================================================
# SAVE RECEIPT
# ============================================================

def save_receipt(
    worksheet,
    transaction_date,
    store,
    category,
    items,
    tax,
    discount,
    payment,
    note,
):

    receipt_id = str(uuid.uuid4())[:8]

    subtotal_total = sum(
        item["qty"] * item["price"]
        for item in items
    )

    total = subtotal_total + tax - discount

    rows = []

    for item in items:

        subtotal = (
            item["qty"] * item["price"]
        )

        rows.append([
            receipt_id,
            transaction_date,
            store,
            category,
            item["item"],
            item["qty"],
            item["price"],
            subtotal,
            tax,
            discount,
            total,
            payment,
            note,
        ])

    worksheet.append_rows(
        rows,
        value_input_option="USER_ENTERED",
    )

    return total


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="Receipt Tracker",
    page_icon="🧾",
    layout="wide",
)


# ============================================================
# TITLE
# ============================================================

st.title("🧾 Receipt Tracker")

st.caption(
    "Simple receipt tracking system using Streamlit and Google Sheets."
)


# ============================================================
# CONNECT
# ============================================================

try:

    worksheet = connect_google_sheet()

except Exception as e:

    st.error(
        "Gagal terhubung ke Google Sheets."
    )

    st.exception(e)

    st.stop()


# ============================================================
# SIDEBAR
# ============================================================

page = st.sidebar.radio(
    "Menu",
    [
        "Dashboard",
        "Tambah Receipt",
        "Riwayat Receipt",
    ],
)


# ============================================================
# DASHBOARD
# ============================================================

if page == "Dashboard":

    st.header("Dashboard")

    df = load_data(worksheet)

    if df.empty:

        st.info(
            "Belum terdapat data receipt."
        )

    else:

        df["Total"] = pd.to_numeric(
            df["Total"],
            errors="coerce",
        )

        # Karena setiap item mempunyai ID receipt yang sama,
        # ambil satu total untuk setiap receipt.
        receipt_totals = (
            df.groupby("ID")["Total"]
            .first()
        )

        total_expense = receipt_totals.sum()

        total_receipts = df["ID"].nunique()

        col1, col2 = st.columns(2)

        with col1:

            st.metric(
                "Total Pengeluaran",
                f"Rp {total_expense:,.0f}",
            )

        with col2:

            st.metric(
                "Total Receipt",
                total_receipts,
            )

        st.divider()

        category_data = (
            df.groupby("ID")
            .first()
            .groupby("Kategori")["Total"]
            .sum()
            .sort_values(ascending=False)
        )

        st.subheader(
            "Pengeluaran Berdasarkan Kategori"
        )

        st.bar_chart(
            category_data
        )


# ============================================================
# ADD RECEIPT
# ============================================================

elif page == "Tambah Receipt":

    st.header("Tambah Receipt")

    col1, col2 = st.columns(2)

    with col1:

        transaction_date = st.date_input(
            "Tanggal",
            value=date.today(),
        )

        store = st.text_input(
            "Nama Toko / Vendor",
            placeholder="Contoh: Indomaret",
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
                "Other",
            ],
        )

    with col2:

        payment = st.selectbox(
            "Metode Pembayaran",
            [
                "Cash",
                "Debit",
                "Credit Card",
                "E-Wallet",
                "Bank Transfer",
            ],
        )

        tax = st.number_input(
            "Pajak",
            min_value=0,
            value=0,
            step=1000,
        )

        discount = st.number_input(
            "Diskon",
            min_value=0,
            value=0,
            step=1000,
        )

    st.divider()

    # --------------------------------------------------------
    # SESSION STATE
    # --------------------------------------------------------

    if "items" not in st.session_state:

        st.session_state.items = []


    st.subheader("Daftar Item")

    col1, col2, col3 = st.columns([3, 1, 2])

    with col1:

        item_name = st.text_input(
            "Nama Item",
        )

    with col2:

        qty = st.number_input(
            "Qty",
            min_value=1,
            value=1,
            step=1,
        )

    with col3:

        price = st.number_input(
            "Harga",
            min_value=0,
            value=0,
            step=1000,
        )


    if st.button(
        "➕ Tambah Item"
    ):

        if not item_name:

            st.warning(
                "Nama item harus diisi."
            )

        elif price <= 0:

            st.warning(
                "Harga harus lebih dari 0."
            )

        else:

            st.session_state.items.append({
                "item": item_name,
                "qty": qty,
                "price": price,
            })

            st.success(
                "Item berhasil ditambahkan."
            )


    # --------------------------------------------------------
    # ITEMS
    # --------------------------------------------------------

    if st.session_state.items:

        st.subheader(
            "Item Receipt"
        )

        table_data = []

        subtotal_total = 0

        for item in st.session_state.items:

            subtotal = (
                item["qty"] *
                item["price"]
            )

            subtotal_total += subtotal

            table_data.append({
                "Item": item["item"],
                "Qty": item["qty"],
                "Harga": item["price"],
                "Subtotal": subtotal,
            })


        df_items = pd.DataFrame(
            table_data
        )

        st.dataframe(
            df_items,
            use_container_width=True,
            hide_index=True,
        )


        total = (
            subtotal_total
            + tax
            - discount
        )


        col1, col2, col3 = st.columns(3)

        with col1:

            st.metric(
                "Subtotal",
                f"Rp {subtotal_total:,.0f}",
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


        note = st.text_area(
            "Catatan"
        )


        if st.button(
            "💾 Simpan Receipt",
            type="primary",
        ):

            if not store:

                st.warning(
                    "Nama toko harus diisi."
                )

            else:

                try:

                    saved_total = save_receipt(
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
                        note=note,
                    )

                    st.success(
                        f"Receipt berhasil disimpan. "
                        f"Total: Rp {saved_total:,.0f}"
                    )

                    st.session_state.items = []

                    st.rerun()

                except Exception as e:

                    st.error(
                        "Gagal menyimpan receipt."
                    )

                    st.exception(e)


# ============================================================
# HISTORY
# ============================================================

elif page == "Riwayat Receipt":

    st.header("Riwayat Receipt")

    df = load_data(worksheet)

    if df.empty:

        st.info(
            "Belum terdapat receipt."
        )

    else:

        search = st.text_input(
            "🔍 Cari berdasarkan toko"
        )

        if search:

            df = df[
                df["Toko"]
                .astype(str)
                .str.contains(
                    search,
                    case=False,
                    na=False,
                )
            ]

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
        )
