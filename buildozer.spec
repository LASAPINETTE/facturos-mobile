[app]

title = Facturos Mobile
package.name = facturos
package.domain = org.facturos
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,txt

version = 0.1
requirements = python3,kivy,kivymd,requests,datetime,sqlite3,plyer,threading,matplotlib,fpdf,barcode,Pillow,pyzbar

orientation = portrait
fullscreen = 1

android.permissions = INTERNET, ACCESS_NETWORK_STATE, ACCESS_WIFI_STATE
android.api = 30
android.minapi = 21
android.ndk = 23b
android.sdk = 30

icon.filename = icone.png
presplash.filename = icone.png

android.arch = arm64-v8a
android.version_code = 1
android.version_name = 1.0