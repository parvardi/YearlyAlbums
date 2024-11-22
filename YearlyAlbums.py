import os
import streamlit as st
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from datetime import datetime
from collections import defaultdict
from PIL import Image, ImageDraw, ImageFont
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from io import BytesIO
import secrets

# ---------------------------- Configuration ----------------------------

# Define Spotify OAuth Scope
SCOPE = "user-top-read"

# Initialize SpotifyOAuth with appropriate parameters
sp_oauth = SpotifyOAuth(
    client_id=os.getenv("SPOTIPY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
    scope=SCOPE,
    cache_path=None  # Disable Spotipy's internal cache
)

# ---------------------------- Streamlit App UI ----------------------------

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

# ---------------------------- OAuth Handling ----------------------------

def generate_state():
    """Generate a random string for the state parameter."""
    return secrets.token_urlsafe(16)

def get_authorization_url(state):
    """Generate the Spotify authorization URL with the given state."""
    auth_url = sp_oauth.get_authorize_url(state=state)
    return auth_url

def get_token_from_code(code):
    """Exchange the authorization code for an access token."""
    token_info = sp_oauth.get_access_token(code, as_dict=True)
    return token_info

# Parse query parameters to get the authorization code and state
query_params = st.query_params
code = query_params.get("code")
received_state = query_params.get("state")

# Initialize session state variables if they don't exist
if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False
if 'token_info' not in st.session_state:
    st.session_state['token_info'] = None
if 'oauth_state' not in st.session_state:
    st.session_state['oauth_state'] = None

# ---------------------------- Function Definitions ----------------------------

@st.cache_data(ttl=0)
def get_top_albums(sp):
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

def overlay_text_on_image(img, album_name, artist_name):
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
                    response = requests.get(image_url)
                    img = Image.open(BytesIO(response.content)).resize(image_size)
                    img_with_text = overlay_text_on_image(img, album_name, artist_name)
                else:
                    img_with_text = Image.new("RGB", image_size, color='gray')
            else:
                # Placeholder for empty slots
                img_with_text = Image.new("RGB", image_size, color='white')

            composite_image.paste(img_with_text, (x, y))

        y_offset += image_size[1] + margin + padding_top

    return composite_image

# ---------------------------- Authentication Flow ----------------------------

def authenticate():
    if not st.session_state['authenticated']:
        state = generate_state()
        st.session_state['oauth_state'] = state
        auth_url = get_authorization_url(state)
        st.markdown(f"[**Authenticate with Spotify**]({auth_url})")
    else:
        st.success(f"Authenticated as {st.session_state['user_name']}")

# ---------------------------- Main Application Logic ----------------------------

# Check if there's an authorization code in the URL
if code and not st.session_state['authenticated']:
    try:
        # Verify state parameter
        if received_state and st.session_state['oauth_state'] == received_state:
            token_info = get_token_from_code(code[0])
            st.session_state['token_info'] = token_info
            st.session_state['authenticated'] = True

            # Initialize Spotipy with the obtained token
            sp = spotipy.Spotify(auth=token_info['access_token'])

            # Get current user
            user = sp.current_user()
            st.session_state['user_name'] = user['display_name']
            st.success(f"Authenticated as {user['display_name']}")

            # Clear OAuth state
            st.session_state['oauth_state'] = None

        else:
            st.error("State parameter mismatch. Potential CSRF attack detected.")
    except Exception as e:
        st.error(f"Authentication failed: {e}")
else:
    # If not authenticated, show the authenticate button
    if not st.session_state['authenticated']:
        authenticate()

# If authenticated, proceed to fetch and display data
if st.session_state['authenticated']:
    # Initialize Spotipy with the token from session state
    token_info = st.session_state['token_info']
    sp = spotipy.Spotify(auth=token_info['access_token'])

    # Fetch top albums if not already fetched
    if 'top_albums' not in st.session_state:
        top_albums = get_top_albums(sp)
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
                        response = requests.get(image_url)
                        img = Image.open(BytesIO(response.content))
                        # Resize image to fit the column
                        img = img.resize((image_width, image_width))
                        # Overlay text
                        img_with_text = overlay_text_on_image(img, album_name, artist_name)
                    else:
                        img_with_text = Image.new("RGB", (300, 300), color='gray')

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
