import os
import streamlit as st
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import CacheHandler
from datetime import datetime
from collections import defaultdict
from PIL import Image, ImageDraw, ImageFont
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from io import BytesIO
import base64
from urllib.parse import urlencode, urlparse, parse_qs

# --------------------------
# Custom Cache Handler
# --------------------------

class StreamlitCacheHandler(CacheHandler):
    """
    Custom cache handler to store and retrieve token information
    using Streamlit's session state.
    """
    def __init__(self):
        if 'token_info' not in st.session_state:
            st.session_state['token_info'] = None

    def get_cached_token(self):
        return st.session_state.get('token_info', None)

    def save_token_to_cache(self, token_info):
        st.session_state['token_info'] = token_info

# --------------------------
# Helper Functions
# --------------------------

def get_env_variable(var_name):
    """
    Fetch environment variable or display error and stop the app.
    """
    value = os.getenv(var_name)
    if not value:
        st.error(f"Environment variable '{var_name}' not set.")
        st.stop()
    return value

def get_url_parameters():
    """
    Retrieve URL query parameters using the updated st.query_params.
    """
    return st.query_params

def authorize():
    """
    Generate and display the Spotify authorization URL.
    """
    auth_url = sp_oauth.get_authorize_url()
    st.markdown(f'[Authorize with Spotify]({auth_url})', unsafe_allow_html=True)

def get_token():
    """
    Exchange authorization code for access token.
    """
    params = get_url_parameters()
    code = params.get('code')
    if code:
        code = code[0]
        try:
            # Remove 'as_dict=True' as per the deprecation warning
            token_info = sp_oauth.get_access_token(code)
            # Since 'as_dict' is deprecated, we need to manually parse the response
            # However, depending on the spotipy version, this might vary
            # For compatibility, we'll assume token_info is a dict
            if isinstance(token_info, str):
                # If a string is returned, parse it into a dict
                token_info = {'access_token': token_info}
            st.session_state['token_info'] = token_info
            st.success("Successfully authenticated with Spotify!")
            # Clear query parameters to prevent reuse of the code
            clear_query_params()
        except Exception as e:
            st.error(f"Failed to get access token: {e}")
            return None
    else:
        authorize()
        st.stop()

def refresh_token():
    """
    Refresh the Spotify access token if expired.
    """
    try:
        token_info = sp.auth_manager.refresh_access_token(st.session_state['token_info']['refresh_token'])
        st.session_state['token_info'] = token_info
    except Exception as e:
        st.error(f"Failed to refresh token: {e}")
        st.session_state['token_info'] = None

def clear_query_params():
    """
    Clear query parameters from the URL after processing.
    """
    st.experimental_set_query_params()

def logout():
    """
    Logout function to clear session state and revoke tokens.
    """
    st.session_state['token_info'] = None
    st.experimental_set_query_params()
    st.success("Logged out successfully!")
    st.experimental_rerun()

# --------------------------
# Initialize Spotify OAuth
# --------------------------

# Initialize the custom cache handler
cache_handler = StreamlitCacheHandler()

# Set up Spotify authentication using Spotipy with the custom cache handler
sp_oauth = SpotifyOAuth(
    client_id=get_env_variable("SPOTIPY_CLIENT_ID"),
    client_secret=get_env_variable("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=get_env_variable("SPOTIPY_REDIRECT_URI"),  # Should include trailing slash
    scope="user-top-read",
    cache_handler=cache_handler,
    show_dialog=True  # Set to False in production to reuse existing tokens
)

# Configure retries and increased timeout for Spotipy
session = requests.Session()
retries = Retry(
    total=5,  # Retry up to 5 times
    backoff_factor=0.5,  # Exponential backoff: 0.5s, 1s, 2s, etc.
    status_forcelist=[500, 502, 503, 504],  # Retry for server errors
)
adapter = HTTPAdapter(max_retries=retries)
session.mount('https://', adapter)

# Create a Spotipy object with the custom session and timeout
sp = Spotify(
    auth_manager=sp_oauth,
    requests_timeout=30,  # Increased timeout to 30 seconds
    requests_session=session,
)

# --------------------------
# Streamlit App UI
# --------------------------

st.set_page_config(layout="wide")
st.title("Spotify Top Albums of the Year")
st.write("Find your top Spotify albums released between December 2023 and December 2024.")

# Add a slider for user input
max_albums_per_month = st.slider(
    "How many of your top albums do you want to see per month?",
    min_value=3,
    max_value=10,
    value=5,
    step=1
)

# Optional: Add a logout button
if st.button("Logout"):
    logout()

# --------------------------
# Authentication Flow
# --------------------------

# Check if the user is authenticated
if st.session_state['token_info'] is None:
    get_token()
else:
    # Check if token is expired
    if sp.auth_manager.is_token_expired(st.session_state['token_info']):
        refresh_token()
        if st.session_state['token_info'] is None:
            get_token()

# Set the token for Spotipy
sp.auth_manager.token_info = st.session_state['token_info']

# Now you can safely call Spotify API methods
try:
    user = sp.current_user()
    st.write(f"Authenticated as **{user['display_name']}**")
except Exception as e:
    st.error(f"Error fetching user info: {e}")
    st.stop()

# --------------------------
# Function to Get Top Albums
# --------------------------

@st.cache_data(ttl=0)
def get_top_albums():
    try:
        top_albums = defaultdict(list)
        current_year = datetime.now().year
        seen_albums = set()

        offset = 0
        all_tracks = []

        while True:
            # Fetch top tracks from Spotify API
            top_tracks = sp.current_user_top_tracks(limit=50, offset=offset, time_range="long_term")
            all_tracks.extend(top_tracks['items'])
            if len(top_tracks['items']) < 50:
                break
            offset += 50

        for track in all_tracks:
            album = track['album']
            album_name = album['name']
            release_date = album['release_date']
            album_images = album['images']
            album_image_url = album_images[0]['url'] if album_images else None
            album_artists = ", ".join([artist['name'] for artist in album['artists']])
            total_tracks = album['total_tracks']

            # Skip albums with fewer than 3 tracks
            if total_tracks < 3:
                continue

            try:
                if len(release_date) == 4:
                    played_at = datetime.strptime(release_date, "%Y")
                elif len(release_date) == 7:
                    played_at = datetime.strptime(release_date, "%Y-%m")
                else:
                    played_at = datetime.strptime(release_date, "%Y-%m-%d")

                if played_at.year <= 1900 or played_at.year > datetime.now().year:
                    raise ValueError(f"Invalid year: {played_at.year}")

            except ValueError as e:
                print(f"Error parsing release date '{release_date}': {e}")
                continue

            if (played_at.year == current_year) or (played_at.year == current_year - 1 and played_at.month == 12):
                month_str = played_at.strftime("%m/%y")
                if album_name not in seen_albums:
                    top_albums[month_str].append({
                        'name': album_name,
                        'artist': album_artists,
                        'image_url': album_image_url
                    })
                    seen_albums.add(album_name)

        months = []
        months.append(datetime(current_year - 1, 12, 1).strftime("%m/%y"))
        for month_num in range(1, 13):
            date = datetime(current_year, month_num, 1)
            months.append(date.strftime("%m/%y"))

        ordered_top_albums = {month: top_albums.get(month, []) for month in months}
        return ordered_top_albums

    except requests.exceptions.ReadTimeout:
        st.error("The request to Spotify timed out. Please try again later.")
        return {}
    except Exception as e:
        st.error(f"An unexpected error occurred: {e}")
        return {}

# --------------------------
# Image Overlay and Composite Functions
# --------------------------

def overlay_text_on_image(img, album_name, artist_name):
    """
    Overlay album name and artist name on the album cover image.
    """
    img = img.convert("RGBA")
    txt = Image.new("RGBA", img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(txt)

    font_size = int(img.size[1] * 0.05)
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except IOError:
        font = ImageFont.load_default()

    max_length = 20

    def truncate_text(text):
        return (text[:max_length] + '...') if len(text) > max_length else text

    album_name_truncated = truncate_text(album_name)
    artist_name_truncated = truncate_text(artist_name)

    # Calculate positions for text
    padding = int(img.size[1] * 0.02)  # Small padding from the top and left
    artist_position = (padding, padding)  # Artist name at the top-left
    album_position = (padding, padding + font_size + 5)  # Album title below artist name

    # Draw artist name
    draw.text(artist_position, artist_name_truncated, font=font, fill=(255, 255, 255, 200))

    # Draw album title
    draw.text(album_position, album_name_truncated, font=font, fill=(255, 255, 255, 200))

    combined = Image.alpha_composite(img, txt)
    return combined

def create_composite_image(albums_by_month, max_albums_per_month):
    """
    Create a composite image of top albums grouped by month.
    """
    image_size = (300, 300)
    margin = 10
    padding_top = 50  # Space for month labels
    font_size = 40

    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except IOError:
        font = ImageFont.load_default()

    # Calculate composite image size
    num_months = len(albums_by_month)
    composite_width = max_albums_per_month * (image_size[0] + margin) + margin
    composite_height = num_months * (image_size[1] + margin + padding_top) + margin

    composite_image = Image.new('RGB', (composite_width, composite_height), color='white')
    draw = ImageDraw.Draw(composite_image)

    y_offset = margin

    for month, albums in albums_by_month:
        # Draw the month label
        draw.text((margin, y_offset), month, font=font, fill=(0, 0, 0))

        y = y_offset + padding_top
        for idx, album_info in enumerate(albums):
            x = margin + idx * (image_size[0] + margin)
            if album_info is not None:
                album_name = album_info['name']
                artist_name = album_info['artist']
                image_url = album_info['image_url']

                if image_url:
                    try:
                        response = requests.get(image_url, timeout=10)
                        response.raise_for_status()
                        img = Image.open(BytesIO(response.content)).resize(image_size)
                        img_with_text = overlay_text_on_image(img, album_name, artist_name)
                    except Exception as e:
                        st.warning(f"Failed to load image for album '{album_name}': {e}")
                        img_with_text = Image.new("RGB", image_size, color='gray')
                else:
                    img_with_text = Image.new("RGB", image_size, color='gray')
            else:
                # Placeholder for empty slots
                img_with_text = Image.new("RGB", image_size, color='white')

            composite_image.paste(img_with_text, (x, y))

        y_offset += image_size[1] + margin + padding_top

    return composite_image

# --------------------------
# Main Application Logic
# --------------------------

# Fetch top albums if not already fetched
if 'top_albums' not in st.session_state:
    top_albums = get_top_albums()
    st.session_state['top_albums'] = top_albums
else:
    top_albums = st.session_state['top_albums']

# Display the original album grid
for month, albums in top_albums.items():
    col1, col2 = st.columns([0.5, 9.5])

    with col1:
        st.write(f"**{month}**")

    with col2:
        if albums:
            albums = albums[:max_albums_per_month]
            num_albums = len(albums)
            cols = st.columns(num_albums, gap="small")
            max_image_width = 800
            image_width = int(max_image_width / max_albums_per_month) - 10

            for idx, album_info in enumerate(albums):
                album_name = album_info['name']
                artist_name = album_info['artist']
                image_url = album_info['image_url']

                if image_url:
                    try:
                        response = requests.get(image_url, timeout=10)
                        response.raise_for_status()
                        img = Image.open(BytesIO(response.content))
                        # Resize image to fit the column
                        img = img.resize((image_width, image_width))
                        # Overlay text
                        img_with_text = overlay_text_on_image(img, album_name, artist_name)
                    except Exception as e:
                        st.warning(f"Failed to load image for album '{album_name}': {e}")
                        img_with_text = Image.new("RGB", (image_width, image_width), color='gray')
                else:
                    img_with_text = Image.new("RGB", (image_width, image_width), color='gray')

                with cols[idx]:
                    st.image(img_with_text, use_container_width=True)
        else:
            st.write("No top albums for this month.")

# Collect albums grouped by month with placeholders
albums_by_month = []
for month in top_albums:
    month_albums = top_albums[month][:max_albums_per_month]
    # Pad with None to reach max_albums_per_month
    while len(month_albums) < max_albums_per_month:
        month_albums.append(None)
    albums_by_month.append((month, month_albums))

if albums_by_month:
    if 'byte_im' not in st.session_state:
        composite_image = create_composite_image(albums_by_month, max_albums_per_month)

        buf = BytesIO()
        composite_image.save(buf, format="PNG")
        byte_im = buf.getvalue()
        st.session_state['byte_im'] = byte_im
    else:
        byte_im = st.session_state['byte_im']

    # Place the download button after the album grid
    st.download_button(
        label="Download Image",
        data=byte_im,
        file_name="top_albums.png",
        mime="image/png"
    )
