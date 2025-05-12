import logging
from flask import Flask, render_template, request, session, jsonify, redirect, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman
from flask_caching import Cache
import requests
from datetime import datetime
import pytz
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'cuacaku-app-default-key')

# Get API Key
API_KEY = os.environ.get('OPENWEATHER_API_KEY')
if not API_KEY:
    raise ValueError("API key tidak ditemukan. Pastikan OPENWEATHER_API_KEY terdapat dalam file .env")

def get_city_name(lat, lon):
    """Dapatkan nama kota berdasarkan koordinat."""
    try:
        url = f"http://api.openweathermap.org/geo/1.0/reverse?lat={lat}&lon={lon}&limit=1&appid={API_KEY}"
        response = requests.get(url)
        if response.status_code != 200:
            return "Lokasi Tidak Diketahui"
        
        data = response.json()
        return data[0]["name"] if data and len(data) > 0 else "Lokasi Tidak Diketahui"
    except Exception as e:
        print(f"Error getting city name: {str(e)}")
        return "Lokasi Tidak Diketahui"

def get_local_time(timezone_offset):
    """Dapatkan waktu lokal dari timezone offset."""
    try:
        # Konversi offset UTC dalam detik ke jam
        hours = int(timezone_offset / 3600)
        sign = '-' if hours < 0 else '+'
        abs_hours = abs(hours)
        tz_name = f"Etc/GMT{sign}{abs_hours}"
        
        try:
            tz = pytz.timezone(tz_name)
        except pytz.exceptions.UnknownTimeZoneError:
            tz = pytz.UTC
        
        # Format waktu dengan zona waktu yang benar
        local_time = datetime.now(tz)
        return local_time.strftime("%A, %d %B %Y %H:%M")
    except Exception as e:
        print(f"Error in get_local_time: {str(e)}")
        return datetime.now().strftime("%A, %d %B %Y %H:%M")

def get_weather_warning(weather_data):
    """Dapatkan peringatan cuaca berdasarkan kondisi."""
    warnings = []
    
    # Cek suhu ekstrem
    temp = weather_data["main"]["temp"]
    if temp > 35:
        warnings.append({
            "level": "tinggi",
            "type": "suhu_panas",
            "message": "Suhu sangat tinggi! Hindari aktivitas luar ruangan.",
            "icon": "fa-temperature-high",
            "color": "orange"
        })
    elif temp < 5:
        warnings.append({
            "level": "rendah",
            "type": "suhu_dingin",
            "message": "Suhu sangat rendah! Kenakan pakaian hangat.",
            "icon": "fa-temperature-low",
            "color": "blue"
        })
    
    # Cek kondisi cuaca
    weather_id = weather_data["weather"][0]["id"]
    if 500 <= weather_id < 600:  # Hujan
        if weather_id >= 502:  # Hujan lebat
            warnings.append({
                "level": "sedang",
                "type": "hujan_lebat",
                "message": "Hujan lebat! Siapkan payung atau jas hujan.",
                "icon": "fa-cloud-rain",
                "color": "blue"
            })
    
    # Cek angin kencang
    if "wind" in weather_data and weather_data["wind"]["speed"] > 10:
        warnings.append({
            "level": "sedang",
            "type": "angin_kencang",
            "message": "Angin kencang! Berhati-hatilah.",
            "icon": "fa-wind",
            "color": "gray"
        })
    
    return warnings

def get_uv_and_air_quality(lat, lon):
    """Dapatkan informasi UV dan kualitas udara."""
    try:
        uv_url = f"http://api.openweathermap.org/data/2.5/uvi?appid={API_KEY}&lat={lat}&lon={lon}"
        air_quality_url = f"http://api.openweathermap.org/data/2.5/air_pollution?appid={API_KEY}&lat={lat}&lon={lon}"
        
        uv_response = requests.get(uv_url)
        air_quality_response = requests.get(air_quality_url)
        
        uv_data = uv_response.json()
        air_quality_data = air_quality_response.json()
        
        # Interpretasi UV Index
        uv_index = uv_data.get('value', 0)
        uv_category = "Rendah"
        if 3 <= uv_index < 6:
            uv_category = "Sedang"
        elif 6 <= uv_index < 8:
            uv_category = "Tinggi"
        elif 8 <= uv_index < 11:
            uv_category = "Sangat Tinggi"
        elif uv_index >= 11:
            uv_category = "Ekstrem"
        
        # Interpretasi Kualitas Udara
        aqi = air_quality_data['list'][0]['main']['aqi']
        air_quality_levels = {
            1: "Baik",
            2: "Sedang",
            3: "Tidak Sehat untuk Kelompok Sensitif",
            4: "Tidak Sehat",
            5: "Berbahaya"
        }
        
        return {
            "uv_index": uv_index,
            "uv_category": uv_category,
            "air_quality": air_quality_levels.get(aqi, "Tidak Diketahui")
        }
    except Exception as e:
        return {
            "error": f"Gagal mendapatkan data UV dan kualitas udara: {str(e)}"
        }

def save_search_history(city_name):
    """Simpan riwayat pencarian."""
    if 'search_history' not in session:
        session['search_history'] = []
    
    # Hindari duplikasi dalam riwayat
    if city_name not in session['search_history']:
        # Batasi jumlah riwayat hingga 5
        if len(session['search_history']) >= 5:
            session['search_history'].pop()
        
        # Tambahkan pencarian baru ke awal list
        session['search_history'].insert(0, city_name)
        session.modified = True

@app.route("/clear-history", methods=["POST"])
def clear_history():
    """Hapus riwayat pencarian."""
    if 'search_history' in session:
        session.pop('search_history')
    return jsonify({"success": True})

@app.route("/", methods=["GET", "POST"])
def index():
    """Route utama untuk menampilkan cuaca."""
    weather = None
    forecast = None
    uv_air_quality = None
    warnings = None
    
    if request.method == "POST":
        city = request.form["city"]
        
        try:
            # Jika city berisi lat,long, maka kita akan mengonversinya
            if ',' in city:
                try:
                    lat, lon = map(float, city.split(','))
                    city_name = get_city_name(lat, lon)
                    weather_url = f"http://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={API_KEY}&units=metric&lang=id"
                    forecast_url = f"http://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={API_KEY}&units=metric&lang=id"
                except ValueError:
                    return render_template("index.html", 
                                          weather={"error": "Format koordinat tidak valid"},
                                          search_history=session.get('search_history', []))
            else:
                city_name = city
                weather_url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={API_KEY}&units=metric&lang=id"
                forecast_url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={API_KEY}&units=metric&lang=id"

            # Dapatkan data cuaca saat ini
            weather_response = requests.get(weather_url)
            weather_data = weather_response.json()

            if weather_data["cod"] == 200:
                # Dapatkan data forecast
                forecast_response = requests.get(forecast_url)
                forecast_data = forecast_response.json()
                
                # Dapatkan waktu lokal
                local_time = get_local_time(weather_data.get("timezone", 0))
                
                # Data cuaca saat ini
                weather = {
                    "city": city_name,
                    "temperature": round(weather_data["main"]["temp"]),
                    "feels_like": round(weather_data["main"]["feels_like"]),
                    "min_temp": round(weather_data["main"]["temp_min"]),
                    "max_temp": round(weather_data["main"]["temp_max"]),
                    "humidity": weather_data["main"]["humidity"],
                    "wind_speed": weather_data["wind"]["speed"],
                    "pressure": weather_data["main"]["pressure"],
                    "description": weather_data["weather"][0]["description"],
                    "main": weather_data["weather"][0]["main"],
                    "icon": weather_data["weather"][0]["icon"],
                    "local_time": local_time,
                    "visibility": weather_data.get("visibility", 0) / 1000,
                    "sunrise": datetime.fromtimestamp(weather_data["sys"]["sunrise"]).strftime("%H:%M"),
                    "sunset": datetime.fromtimestamp(weather_data["sys"]["sunset"]).strftime("%H:%M"),
                }

                # Dapatkan peringatan cuaca
                warnings = get_weather_warning(weather_data)

                # Jika koordinat tersedia, dapatkan UV dan kualitas udara
                if ',' in city:
                    lat, lon = map(float, city.split(','))
                    uv_air_quality = get_uv_and_air_quality(lat, lon)

                # Data prediksi cuaca
                forecast = []
                unique_dates = set()
                
                for day in forecast_data["list"]:
                    date = datetime.utcfromtimestamp(day["dt"]).strftime('%Y-%m-%d')
                    
                    # Hanya ambil satu data per hari
                    if date not in unique_dates and len(unique_dates) < 5:
                        unique_dates.add(date)
                        
                        day_forecast = {
                            "date": datetime.utcfromtimestamp(day["dt"]).strftime('%A, %d %B'),
                            "temperature": round(day["main"]["temp"]),
                            "min_temp": round(day["main"]["temp_min"]),
                            "max_temp": round(day["main"]["temp_max"]),
                            "humidity": day["main"]["humidity"],
                            "description": day["weather"][0]["description"],
                            "main": day["weather"][0]["main"],
                            "icon": day["weather"][0]["icon"],
                            "wind_speed": day["wind"]["speed"],
                        }
                        forecast.append(day_forecast)
                
                # Simpan pencarian ke riwayat
                save_search_history(city_name)
            else:
                weather = {"error": f"Kota '{city}' tidak ditemukan. Mohon periksa ejaan atau coba kota lain."}
        
        except Exception as e:
            weather = {"error": f"Terjadi kesalahan: {str(e)}"}
    
    return render_template("index.html", 
                           weather=weather, 
                           forecast=forecast, 
                           search_history=session.get('search_history', []),
                           warnings=warnings,
                           uv_air_quality=uv_air_quality)

@app.errorhandler(404)
def page_not_found(e):
    """Handler untuk error 404."""
    return render_template('index.html', 
                           weather={"error": "Halaman tidak ditemukan"}, 
                           search_history=session.get('search_history', [])), 404

@app.errorhandler(500)
def internal_server_error(e):
    """Handler untuk error 500."""
    return render_template('index.html', 
                           weather={"error": "Terjadi kesalahan server"}, 
                           search_history=session.get('search_history', [])), 500



# Konfigurasi Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('cuacaku.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    app.run(debug=True)

# Konfigurasi Keamanan
app.config.update(
    SECRET_KEY=os.environ.get('SECRET_KEY', os.urandom(24)),
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
)

# Tambahkan HTTP headers keamanan
Talisman(app, 
    content_security_policy={
        'default-src': '\'self\'',
        'script-src': [
            '\'self\'', 
            'https://cdn.jsdelivr.net', 
            'https://cdnjs.cloudflare.com'
        ],
        'style-src': [
            '\'self\'', 
            'https://fonts.googleapis.com', 
            'https://cdnjs.cloudflare.com'
        ],
        'font-src': [
            '\'self\'', 
            'https://fonts.gstatic.com'
        ]
    },
    force_https=True
)

# Konfigurasi Rate Limiting
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["100 per day", "30 per hour"],
    storage_uri="memory://"
)

# Konfigurasi Caching
cache = Cache(app, config={
    'CACHE_TYPE': 'SimpleCache',
    'CACHE_DEFAULT_TIMEOUT': 300  # 5 menit
})

# Fungsi untuk melakukan validasi input
def validate_city_input(city):
    """
    Validasi input kota
    - Panjang maksimum 100 karakter
    - Hanya huruf, spasi, dan tanda hubung
    """
    if not city:
        return False
    
    if len(city) > 100:
        return False
    
    # Validasi karakter
    import re
    if not re.match(r'^[a-zA-Z\s\-,\.]+$', city):
        return False
    
    return True

# Fungsi untuk mencatat aktivitas pencarian
def log_weather_search(city, success=True):
    """
    Log aktivitas pencarian cuaca
    """
    log_message = f"Weather Search: City={city}, Success={success}"
    if success:
        logger.info(log_message)
    else:
        logger.warning(log_message)

# Fungsi tambahan untuk menangani error dengan lebih baik
def handle_api_error(error, city):
    """
    Tangani error dari API dengan lebih informatif
    """
    error_mapping = {
        401: "Autentikasi API gagal",
        404: f"Kota '{city}' tidak ditemukan",
        429: "Terlalu banyak permintaan. Coba lagi nanti",
        500: "Kesalahan server internal",
        503: "Layanan tidak tersedia saat ini"
    }
    
    # Log error
    logger.error(f"API Error for city {city}: {error}")
    
    # Kembalikan pesan error yang sesuai
    return error_mapping.get(error, "Terjadi kesalahan yang tidak diketahui")

# Fungsi untuk mengamankan data sensitif
def sanitize_log_data(data):
    """
    Hapus informasi sensitif sebelum logging
    """
    if isinstance(data, dict):
        sanitized = data.copy()
        # Contoh: hapus data pribadi
        sensitive_keys = ['password', 'api_key', 'token']
        for key in sensitive_keys:
            sanitized.pop(key, None)
        return sanitized
    return data

# Tambahkan metode baru untuk statistik
class WeatherSearchTracker:
    _instance = None
    
    def __new__(cls):
        if not cls._instance:
            cls._instance = super(WeatherSearchTracker, cls).__new__(cls)
            cls._instance.searches = {}
        return cls._instance
    
    def record_search(self, city):
        """Catat jumlah pencarian untuk setiap kota"""
        self.searches[city] = self.searches.get(city, 0) + 1
    
    def get_top_searches(self, n=5):
        """Dapatkan kota-kota paling sering dicari"""
        return sorted(self.searches.items(), key=lambda x: x[1], reverse=True)[:n]

# Inisialisasi tracker
search_tracker = WeatherSearchTracker()

# Contoh penggunaan dalam route
@app.route("/top-searches")
def top_searches():
    """Route untuk menampilkan kota-kota paling sering dicari"""
    top = search_tracker.get_top_searches()
    return jsonify(top)

# Tambahkan ini ke route pencarian di app.py sebelumnya
# search_tracker.record_search(city_name)

def get_hourly_temperatures(city=None, lat=None, lon=None):
    """
    Retrieve hourly temperature data for the last 24 hours
    Can be searched by city name or latitude/longitude
    """
    try:
        # Determine the API URL based on input
        if city:
            hourly_url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={API_KEY}&units=metric&lang=id"
        elif lat and lon:
            hourly_url = f"http://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={API_KEY}&units=metric&lang=id"
        else:
            return []

        # Fetch hourly forecast
        hourly_response = requests.get(hourly_url)
        hourly_data = hourly_response.json()

        # Filter and process data for last 24 hours
        current_time = datetime.now(pytz.utc)
        hourly_temps = []

        for forecast in hourly_data.get('list', []):
            forecast_time = datetime.fromtimestamp(forecast['dt'], pytz.utc)
            
            # Only include data from last 24 hours
            if (current_time - forecast_time).total_seconds() <= 86400:
                hourly_temps.append({
                    'time': forecast_time.strftime('%H:%M'),
                    'temperature': round(forecast['main']['temp'], 1)
                })

        # Sort by time and limit to 24 entries
        hourly_temps.sort(key=lambda x: x['time'])
        return hourly_temps[:24]

    except Exception as e:
        print(f"Error fetching hourly temperatures: {str(e)}")
        return []

# Update the index route to include hourly temperatures
@app.route("/", methods=["GET", "POST"])
def index():
    # Existing code...
    
    # Add hourly temperature data
    hourly_temps = []
    if ',' in city:
        try:
            lat, lon = map(float, city.split(','))
            hourly_temps = get_hourly_temperatures(lat=lat, lon=lon)
        except ValueError:
            pass
    else:
        hourly_temps = get_hourly_temperatures(city=city)
    
    return render_template("index.html", 
                           weather=weather, 
                           forecast=forecast, 
                           search_history=session.get('search_history', []),
                           warnings=warnings,
                           uv_air_quality=uv_air_quality,
                           hourly_temps=hourly_temps)

# Update script in HTML to use the hourly_temps
# renderTemperatureChart({{ hourly_temps | tojson | safe }})