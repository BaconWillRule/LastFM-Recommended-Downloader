import os
import re
import datetime
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
import yt_dlp as youtube_dl
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC, USLT

# Load environment variables from .env file
load_dotenv()

# Directory where Navidrome stores music
NAVIDROME_MUSIC_DIR = os.getenv('NAVIDROME_MUSIC_DIR', '/path/to/your/navidrome/music')

# Genius API key and base URL
GENIUS_API_KEY = os.getenv('GENIUS_API_KEY')
GENIUS_API_URL = 'https://api.genius.com'

def sanitize_filename(name):
    unsafe_characters = ['<', '>', ':', '"', '/', '\\', '|', '?', '*']
    for char in unsafe_characters:
        name = name.replace(char, '')
    return name

def get_genius_data(track_name, artist_name):
    headers = {
        'Authorization': f'Bearer {GENIUS_API_KEY}'
    }
    search_url = f'{GENIUS_API_URL}/search'
    params = {
        'q': f'{track_name} {artist_name}'
    }
    response = requests.get(search_url, headers=headers, params=params)
    data = response.json()

    song_info = None
    for hit in data['response']['hits']:
        result = hit['result']
        if (result['title'].lower() == track_name.lower() and
            result['primary_artist']['name'].lower() == artist_name.lower()):
            song_info = result
            break
    
    if song_info is None:
        print(f"Could not find song '{track_name}' by '{artist_name}' on Genius.")
        return None, None

    lyrics_url = song_info['url']
    cover_art_url = song_info['song_art_image_url']
    
    return lyrics_url, cover_art_url

def download_file(url, output_path):
    response = requests.get(url)
    with open(output_path, 'wb') as file:
        file.write(response.content)

def login_to_website():
    chrome_options = Options()
    chrome_options.set_capability("browserName", "chrome")

    driver = None
    tracks = []  # List to store the track information

    try:
        driver = webdriver.Remote(
            command_executor='http://localhost:4444/wd/hub',
            options=chrome_options
        )

        driver.get("https://www.last.fm/login")

        # Wait for and close the cookie consent popup, if present
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, 'onetrust-accept-btn-handler'))
            ).click()
            print("Cookie consent popup closed.")
        except TimeoutException:
            print("No cookie consent popup found, continuing...")

        # Proceed with login
        username_field = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, 'username_or_email'))
        )
        username_field.send_keys(os.environ.get('LASTFM_USERNAME'))

        password_field = driver.find_element(By.NAME, 'password')
        password_field.send_keys(os.environ.get('LASTFM_PASSWORD'))

        login_button = driver.find_element(By.NAME, 'submit')
        login_button.click()

        WebDriverWait(driver, 10).until(EC.url_changes("https://www.last.fm/login"))
        driver.get("https://www.last.fm/home/tracks")

        recs_feed_items = WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".recs-feed-item"))
        )

        for item in recs_feed_items:
            try:
                recs_feed_playlink = item.find_element(By.CSS_SELECTOR, ".recs-feed-playlink").get_attribute("href")
            except NoSuchElementException:
                print("Playlink selector missing")
                continue  # Skip this item if the playlink is missing

            track_name_dirty = item.find_element(By.CSS_SELECTOR, ".recs-feed-title a").text
            track_name = re.sub(r'\s\(\d+:\d+\)$', '', track_name_dirty)

            artist_name = item.find_element(By.CSS_SELECTOR, ".recs-feed-description a").text

            print(f"Playlink: {recs_feed_playlink}, Track Name: {track_name}, Artist Name: {artist_name}")

            tracks.append((recs_feed_playlink, track_name, artist_name))

    except Exception as e:
        print(f"An error occurred: {e}")

    finally:
        if driver is not None:
            driver.quit()

    download_tracks(tracks)

def download_tracks(tracks):
    for playlink, track_name, artist_name in tracks:
        try:
            sanitized_track_name = sanitize_filename(track_name)
            filename = f"{sanitized_track_name}.mp3"
            output_file = os.path.join(NAVIDROME_MUSIC_DIR, filename)

            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': output_file,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'quiet': False,
            }

            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                ydl.download([playlink])

            # Get lyrics and cover art
            lyrics_url, cover_art_url = get_genius_data(track_name, artist_name)

            # Save cover art
            if cover_art_url:
                cover_art_path = os.path.join(NAVIDROME_MUSIC_DIR, f"{sanitized_track_name}_cover.jpg")
                download_file(cover_art_url, cover_art_path)
            else:
                cover_art_path = None

            # Save lyrics
            if lyrics_url:
                lyrics_path = os.path.join(NAVIDROME_MUSIC_DIR, f"{sanitized_track_name}_lyrics.txt")
                download_file(lyrics_url, lyrics_path)
            else:
                lyrics_path = None

            # Add metadata to the MP3 file
            add_metadata(output_file, track_name, artist_name, cover_art_path, lyrics_path)

        except Exception as e:
            print(f"An error occurred while downloading {playlink}: {e}")

def add_metadata(mp3_file, track_name, artist_name, cover_art_path=None, lyrics_path=None):
    audio = ID3(mp3_file)

    # Add or update ID3 tags
    audio['TIT2'] = track_name
    audio['TPE1'] = artist_name

    if cover_art_path:
        with open(cover_art_path, 'rb') as img_file:
            audio.add(APIC(mime='image/jpeg', type=3, desc='Cover', data=img_file.read()))

    if lyrics_path:
        with open(lyrics_path, 'r') as lyrics_file:
            lyrics = lyrics_file.read()
            audio.add(USLT(text=lyrics))

    audio.save()

if __name__ == "__main__":
    login_to_website()
