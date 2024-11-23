# main.py

import os
import warnings
import logging
from datetime import datetime
from collections import defaultdict
from io import BytesIO

import gradio as gr
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
import uvicorn
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import requests
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

# --------------------------
# Load Environment Variables
# --------------------------
load_dotenv()

SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")

# --------------------------
# Suppress Warnings and Configure Logging
# --------------------------
warnings.filterwarnings("ignore", category=DeprecationWarning)
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger("spotify_app")

# --------------------------
# Initialize FastAPI
# --------------------------
app = FastAPI()

# --------------------------
# Initialize Spotify OAuth
# --------------------------
scope = "user-top-read"

sp_oauth = SpotifyOAuth(
    client_id=SPOTIPY_CLIENT_ID,
    client_secret=SPOTIPY_CLIENT_SECRET,
    redirect_uri=SPOTIPY_REDIRECT_URI,
    scope=scope,
    cache_path=".cache",
)

# --------------------------
# Helper Functions
# --------------------------

def authorize_url():
    return sp_oauth.get_authorize_url()

def get_token(code):
    try:
        token_info = sp_oauth.get_access_token(code)
        return token_info
    except Exception as e:
        logger.error(f"Error obtaining access token: {e}")
        return None

def refresh_token(refresh_token):
    try:
        token_info = sp_oauth.refresh_access_token(refresh_token)
        return token_info
    except Exception as e:
        logger.error(f"Error refreshing access token: {e}")
        return None

def fetch_top_albums(token_info, max_albums_per_month):
    sp = spotipy.Spotify(auth=token_info['access_token'])

    try:
        top_tracks = sp.current_user_top_tracks(limit=50, time_range="long_term")
    except spotipy.exceptions.SpotifyException as e:
        logger.error(f"Spotify API error: {e}")
        return {}

    top_albums = defaultdict(list)
    current_year = datetime.now().year
    seen_albums = set()

    for track in top_tracks['items']:
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
            logger.error(f"Error parsing release date '{release_date}': {e}")
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

    # Order months from December of previous year to current month
    months = []
    months.append(datetime(current_year - 1, 12, 1).strftime("%m/%y"))
    for month_num in range(1, 13):
        date = datetime(current_year, month_num, 1)
        months.append(date.strftime("%m/%y"))

    ordered_top_albums = {month: top_albums.get(month, []) for month in months}
    return ordered_top_albums

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
                        logger.warning(f"Failed to load image for album '{album_name}': {e}")
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
# Gradio Interface Functions
# --------------------------

def start_auth():
    """
    Initiate Spotify OAuth flow by redirecting the user to Spotify's authorization URL.
    """
    auth_url = authorize_url()
    return gr.outputs.HTML.update(value=f'<a href="{auth_url}" target="_blank">Click here to authorize with Spotify</a>')

def process_callback(code: str, state: str):
    """
    Process the OAuth callback by exchanging the authorization code for tokens.
    """
    token_info = get_token(code)
    if token_info:
        st.session_state['token_info'] = token_info
        return "Authentication successful! You can close this window and return to the main app."
    else:
        return "Authentication failed. Please try again."

def display_albums(max_albums_per_month: int):
    """
    Fetch and display top albums, then create and provide a downloadable composite image.
    """
    if 'token_info' not in st.session_state:
        return "Not authenticated. Please authorize with Spotify first."

    token_info = st.session_state['token_info']

    # Check if token is expired
    sp_instance = spotipy.Spotify(auth=token_info['access_token'])
    if sp_oauth.is_token_expired(token_info):
        refreshed_token = refresh_token(token_info['refresh_token'])
        if refreshed_token:
            st.session_state['token_info'] = refreshed_token
            token_info = refreshed_token
        else:
            return "Token refresh failed. Please reauthorize with Spotify."

    top_albums = fetch_top_albums(token_info, max_albums_per_month)

    # Create Gradio components
    album_images = []
    for month, albums in top_albums.items():
        month_label = f"**{month}**"
        album_row = gr.Row()
        album_row.append(gr.Markdown(month_label))
        if albums:
            for album in albums[:max_albums_per_month]:
                img = Image.new("RGB", (300, 300), color='gray')  # Placeholder in case of failure
                if album['image_url']:
                    try:
                        response = requests.get(album['image_url'], timeout=10)
                        response.raise_for_status()
                        img = Image.open(BytesIO(response.content))
                        img = overlay_text_on_image(img, album['name'], album['artist'])
                    except Exception as e:
                        logger.warning(f"Failed to load image for album '{album['name']}': {e}")
                        img = Image.new("RGB", (300, 300), color='gray')
                album_row.append(gr.Image(img))
        else:
            album_row.append(gr.Markdown("No top albums for this month."))
        album_images.append(album_row)

    # Create composite image
    composite_image = create_composite_image(list(top_albums.items()), max_albums_per_month)
    buf = BytesIO()
    composite_image.save(buf, format="PNG")
    byte_im = buf.getvalue()

    # Provide download button
    download_button = gr.File.update(value=byte_im, label="Download Composite Image", file_name="top_albums.png")

    return album_images + [download_button]

# --------------------------
# Gradio Interface Layout
# --------------------------

with gr.Blocks() as demo:
    st = gr.State()
    gr.Markdown("# Spotify Top Albums of the Year")
    gr.Markdown("Find your top Spotify albums released between December 2023 and December 2024.")

    with gr.Tab("Authenticate"):
        gr.Markdown("## Step 1: Authorize with Spotify")
        authorize_btn = gr.Button("Authorize with Spotify")
        authorize_output = gr.HTML()
        authorize_btn.click(fn=start_auth, outputs=authorize_output)

    with gr.Tab("View Top Albums"):
        gr.Markdown("## Step 2: View Your Top Albums")
        max_albums_slider = gr.Slider(
            label="How many of your top albums do you want to see per month?",
            minimum=3,
            maximum=10,
            value=5,
            step=1
        )
        view_btn = gr.Button("Fetch and Display Top Albums")
        albums_output = gr.Column()
        download_output = gr.File()
        view_btn.click(fn=display_albums, inputs=max_albums_slider, outputs=albums_output)

    with gr.Tab("OAuth Callback"):
        gr.Markdown("## Spotify OAuth Callback")
        # This tab is just a placeholder. The OAuth callback is handled via FastAPI routes.

# --------------------------
# OAuth Callback Route
# --------------------------

@app.get("/callback")
async def callback(request: Request):
    """
    Handle Spotify OAuth callback.
    """
    code = request.query_params.get("code")
    state = request.query_params.get("state")

    if not code:
        return "Authorization code not found in the callback."

    # Redirect back to Gradio interface
    # Since Gradio and FastAPI are running together, we can set the state
    token_info = get_token(code)
    if token_info:
        # Here, you'd need to find a way to pass the token_info to Gradio's state.
        # This can be complex because Gradio and FastAPI have separate contexts.
        # One approach is to use a shared state mechanism or a database.
        # For simplicity, we'll assume a global variable (not recommended for production).
        st.session_state['token_info'] = token_info
        return RedirectResponse(url="/")
    else:
        return "Failed to obtain access token."

# --------------------------
# Run Gradio with Uvicorn
# --------------------------
if __name__ == "__main__":
    # Launch Gradio app with FastAPI
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="error")
