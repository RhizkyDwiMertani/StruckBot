import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from datetime import date

st.set_page_config(page_title="Pencatat Struk", page_icon="🧾")
st.title("🧾 Catat Struk Belanja")

# Inisialisasi Google Sheets via Streamlit Secrets
@st.cache_resource
def get_google_sheet():
    # Ambil credentials dari Secrets Management Streamlit
    creds_dict = dict(st.secrets["gcp_service_account"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(credentials)
    
    # Buka Google Sheet berdasarkan nama file
    sheet = gc.open(st.secrets["sheet_name"]).sheet1
    return sheet

try:
    sheet = get_google_sheet()
except Exception as e:
    st.error(f"Gagal menghubungkan ke Google Sheets: {e}")
    st.stop()

# Form Input Struk
with st.form("form_struk", clear_on_submit=True):
    tgl = st.date_input("Tanggal Transaksi", value=date.today())
    merchant = st.text_input("Nama Toko / Merchant", placeholder="contoh: Indomaret")
    kategori = st.selectbox("Kategori", ["Konsumsi", "Transportasi", "Kebutuhan Rumah", "Lainnya"])
    nominal = st.number_input("Total Belanja (Rp)", min_value=0, step=1000)
    metode = st.selectbox("Metode Pembayaran", ["QRIS", "Tunai", "Debit/Kredit", "Transfer"])
    catatan = st.text_area("Catatan / Rincian Barang", placeholder="Kopi, sabun, dll.")
    
    submitted = st.form_submit_button("Simpan ke Spreadsheet")
    
    if submitted:
        if merchant and nominal > 0:
            # Format baris: Tanggal, Waktu, Merchant, Kategori, Nominal, Metode, Catatan
            row = [str(tgl), "", merchant, kategori, nominal, metode, catatan]
            sheet.append_row(row)
            st.success("✅ Data berhasil disimpan ke Google Sheets!")
        else:
            st.warning("⚠️ Mohon isi nama toko dan nominal belanja.")
