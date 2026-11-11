# 🎮 Virtual Steering Controller

**Ponsel Anda menjadi setir untuk game simulator PC seperti ETS2 & MudRunner.**

Cara kerjanya:

```
┌─────────────┐   Wi-Fi    ┌──────────────────┐   gamepad virtual   ┌──────────────┐
│ Ponsel      │ ─────────► │ PC: server.py    │ ──────────────────► │ ETS2 /       │
│ (giroskop + │  WebSocket │ (Python + aiohttp│   ViGEmBus (Xbox    │ MudRunner    │
│  tombol)    │  50 Hz     │  + vgamepad)     │   360 virtual)      │              │
└─────────────┘            └──────────────────┘                     └──────────────┘
```

Game melihatnya sebagai **stik gamepad Xbox 360 sungguhan**, jadi tidak perlu
plugin khusus untuk tiap game — semua input cukup lewat standar XInput.

---

## Fitur

- **3 mode setir**: `Putar` (giroskop — putarkan ponsel seperti setir),
  `Miring` (gravitasi — miringkan ponsel), `Sentuh` (seret jari, untuk uji/PC).
- **Kalibrasi tengah**, rentang setir, zona mati, sensitivitas, penghalusan,
  dan balik arah — tersimpan otomatis di ponsel.
- **Tombol lengkap**: gas, rem, klakson, gigi naik/turun, mundur, lampu,
  lampu jauh, rem tangan, mode lampu, lihat kiri/kanan, ganti kamera, jeda.
- **HTTPS otomatis** — sertifikat self-signed dibuat saat pertama dijalankan.
  Ini wajib: browser hanya mengizinkan sensor giroskop di koneksi HTTPS.
- **Aman**: watchdog mereset seluruh input bila ponsel putus / layar mati
  (> 1,5 detik), sehingga gas tidak akan “nyangkut”.
- **GUI status di PC** (URL, indikator koneksi, bar setir/gas/rem) +
  mode konsol untuk server tanpa layar.
- **Uji tanpa ponsel**: keyboard/mouse langsung di browser PC.

---

## Struktur proyek

| File | Fungsi |
|---|---|
| `server.py` | Server utama: HTTP + WebSocket + GUI status |
| `gamepad.py` | Gamepad virtual Xbox (vgamepad/ViGEmBus) + mode demo |
| `web/index.html` | Halaman kontrol di ponsel |
| `web/app.js` | Logika sensor, kalibrasi, tombol, koneksi |
| `web/style.css` | Tampilan ponsel (landscape) |
| `config.json` | Pengaturan server (port, dll.) |
| `requirements.txt` | Dependensi Python |
| `tools/test_client.py` | Simulasi ponsel untuk menguji server |
| `tools/internal_test.py` | Uji mapping gamepad & sesi WebSocket |
| `tools/web_test.js` | Uji logika halaman ponsel (butuh Node.js) |
| `certs/` | Sertifikat HTTPS (dibuat otomatis, boleh dihapus untuk regenerasi) |

---

## Persyaratan

- **PC**: Windows 10/11, terhubung ke Wi-Fi yang sama dengan ponsel.
- **Python 3.10 atau lebih baru** — unduh dari [python.org](https://www.python.org/downloads/),
  saat instal **centang “Add python.exe to PATH”**.
- **Ponsel**: Android (Chrome) atau iPhone (Safari) dengan sensor giroskop.

---

## Instalasi (sekali saja)

Buka Command Prompt di folder proyek, lalu:

```bat
pip install -r requirements.txt
```

Catatan:

- Saat menginstal `vgamepad`, jendela installer **ViGEmBus** akan muncul
  sekali → klik **Install** → **Finish** (ini driver gamepad virtual-nya).
- Bila instalasi vgamepad gagal, instal driver manual dari
  <https://github.com/nefarius/ViGEmBus/releases> lalu ulangi `pip install -r requirements.txt`.

---

## Cara pakai

1. **Jalankan server** di PC:

   ```bat
   python server.py
   ```

   Jendela status akan menampilkan alamat, misalnya:

   ```
   https://192.168.1.5:8000
   ```

   Pastikan kolom **Gamepad** berbunyi *“Gamepad Xbox virtual (ViGEmBus)”*
   (bukan *mode demo*).

2. **Di ponsel**, buka alamat tersebut dengan browser (satu Wi-Fi dengan PC).

3. **Peringatan sertifikat itu normal** (sertifikat buatan sendiri):
   - Android Chrome: **Advanced → Proceed to … (unsafe)**
   - iPhone Safari: **Show Details → visit this website**

4. Tekan **▶ Mulai**, lalu izinkan **sensor gerak / motion & orientation**.

5. Pegang ponsel seperti setir, tekan **Kalibrasi** saat posisi lurus.

6. Buka gamenya. Selesai.

> Tips: aktifkan **layar penuh** (otomatis saat Mulai) supaya tombol tidak
> ketutup gesture navigasi ponsel.

---

## Pengaturan di game

### Euro Truck Simulator 2

1. **Options → Controls**
2. **Controller / Controller type**: pilih **Xbox 360 Controller**
   (virtual pad terdeteksi sebagai stik Xbox).
3. Sumbu yang sudah otomatis:
   - **Steering** → *Left Stick X*
   - **Accelerate** → *Right Trigger (RT)*
   - **Brake/Reverse** → *Left Trigger (LT)*
4. Tombol lain: klik fungsi yang ingin diikat, lalu **tekan tombolnya di
   layar ponsel** (klakson, gigi, lampu, kamera, dll.).
5. Opsional: atur *Steering linearity / sensitivity* sesuai selera —
   default biasanya sudah enak. (Sejak update 1.51 konfigurasi controller
   ETS2 diperbarui; bila preset muncul, pilih preset Xbox lalu sesuaikan.)

### MudRunner

1. **Settings → Controls → Controller**
2. **RT = gas, LT = rem** (default umum).
3. Ikat fungsi lain dengan menekan tombol di ponsel saat diminta.

### Peta tombol (HP → Xbox → saran fungsi game)

| Tombol di ponsel | Tombol Xbox | Fungsi (usulan) |
|---|---|---|
| Pedal GAS / REM | RT / LT | Gas / rem (default di kedua game) |
| Klakson | **A** | Klakson |
| Gigi + / Gigi − | **X / Y** | Naik / turun gigi |
| Mundur | **LB** | Gigi mundur |
| Lampu jauh | **RB** | Lampu jauh (tahan) |
| Lampu | **D-pad atas** | Nyalakan lampu |
| Mode lampu | **D-pad bawah** | Ganti mode lampu |
| ◀ / ▶ | **D-pad kiri/kanan** | Lihat kiri / kanan |
| Kamera | **BACK** | Ganti kamera |
| Jeda | **START** | Menu / jeda |
| Setir (stik kiri) | **Left Stick X** | Menyetir |

Semua ikatan bisa diubah ulang di pengaturan kontrol game.

---

## Mode setir & kalibrasi

| Mode | Cara pakai | Kapan dipakai |
|---|---|---|
| **Putar** (default) | Pegang ponsel tegak menghadap Anda, putarkan seperti setir | Paling mirip setir asli |
| **Miring** | Miringkan ponsel ke kiri/kanan (sedikit datar lebih stabil) | Bila mode Putar tidak responsif |
| **Sentuh** | Seret jari ke kiri/kanan di layar | Tanpa sensor / uji di PC |

- **Kalibrasi** = menetapkan posisi saat ini sebagai “setir lurus”. Lakukan
  setiap kali menggenggam ulang ponsel.
- Jika arah setir terbalik, aktifkan **Balik arah** di panel ⚙.
- **Rentang setir** = berapa derajat gerakan ponsel untuk setir penuh
  (kecil = lebih cepat penuh).
- Bila data sensor hilang, aplikasi otomatis pindah mode + menampilkan pesan.

---

## Perintah & konfigurasi server

```bat
python server.py                    # normal (GUI + HTTPS)
python server.py --port 8080        # ganti port
python server.py --no-gui           # mode konsol
python server.py --no-gamepad       # mode demo tanpa gamepad virtual
python server.py --no-ssl           # HTTP biasa (TIDAK disarankan: sensor
                                    # ponsel biasanya diblokir browser)
python server.py --open             # buka browser otomatis
python server.py --log-level DEBUG  # log detail
```

Pengaturan bawaan juga bisa diubah di `config.json`
(`host`, `port`, `ssl`, `gui`, `open_browser`, `log_level`).

---

## Pengujian

```bat
:: uji server + protokol (tanpa ponsel)
python tools/test_client.py --host 127.0.0.1 --port 8000

:: uji internal (mapping gamepad, sesi, konsistensi HTML/JS) — server harus jalan
python tools/internal_test.py

:: uji logika halaman ponsel (butuh Node.js)
node tools/web_test.js
```

Uji manual: buka `https://localhost:8000` di PC → tekan **Mulai** →
mode **Sentuh**/keyboard aktif otomatis (tanpa giroskop di PC):
`←/→` atau `A/D` setir, `↑/↓` atau `W/S` gas/rem, `Space` klakson,
`Q/E` gigi, `B` rem tangan, `R` mundur, `F` lampu jauh, `L` lampu.

---

## Pemecahan masalah

| Gejala | Penyebab / solusi |
|---|---|
| Halaman tidak terbuka dari ponsel | Firewall Windows → izinkan Python di **jaringan privat**; pastikan PC & ponsel satu Wi-Fi; cek URL IP di jendela server; coba `https://<ip>:8000` |
| “Your connection is not private” | Normal (sertifikat sendiri) → **Advanced → Proceed** |
| Setir tidak bergerak / “sensor tidak ada” | Izinkan sensor gerak (ikon 🔒 di bilah alamat); pakai mode **Miring**; pastikan halaman dibuka via **https**; jangan buka di tab latar belakang |
| Arah setir terbalik | Aktifkan **Balik arah** di panel ⚙, atau kalibrasi ulang |
| Kolom Gamepad = “Mode demo” | `vgamepad`/ViGEmBus gagal dimuat → jalankan ulang `pip install vgamepad`, pastikan installer ViGEmBus selesai, cek **Device Manager → Nefarius Virtual Gamepad Emulation Bus** |
| Gamepad tidak muncul di game | Pilih tipe controller **Xbox 360** di pengaturan game; restart game setelah server berjalan |
| Gas “nyangkut” | Tidak akan terjadi: watchdog mereset input setelah 1,5 detik tanpa data; cek indikator koneksi di ponsel |
| Setir patah-patah / delay | Pakai Wi-Fi 5 GHz, dekatkan ke router, lihat angka RTT di bilah atas ponsel (< 30 ms ideal) |
| Port terpakai | `python server.py --port 8080` |
| IP ponsel & PC beda subnet / pakai SSID tamu | Matikan “Client/AP isolation” router atau gunakan satu SSID yang sama |
| Ingin sertifikat baru (pindah jaringan) | Hapus folder `certs/`, jalankan ulang server |

---

## Keamanan & privasi

- Server hanya melayani **jaringan lokal** — tidak ada data yang dikirim ke
  internet. Input Anda murni menjadi gamepad standar di PC sendiri.
- Sertifikat HTTPS dibuat lokal (self-signed). Hapus `certs/` kapan saja untuk
  membuat ulang.
- Gunakan aplikasi ini untuk keperluan pribadi/simulasi; input yang dikirim
  hanyalah gamepad biasa seperti controller fisik lainnya.

---

## Kredit

- [ViGEmBus](https://github.com/nefarius/ViGEmBus) — driver gamepad virtual Windows (Nefarius)
- [vgamepad](https://github.com/yannbouteiller/vgamepad) — binding Python ViGEmBus (Yann Bouteiller)
- [aiohttp](https://docs.aiohttp.org/) — server HTTP/WebSocket
