# main.py

import os
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

# --------------------------
# Suppress Warnings and Configure Logging
# --------------------------
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger("spotify_app")

# --------------------------
# Initialize FastAPI
# --------------------------
app = FastAPI()

# --------------------------
# Initialize Spotify OAuth
# --------------------------
SPOTIPY_CLIENT_ID = os.environ.get("SPOTIPY_CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.environ.get("SPOTIPY_CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.environ.get("SPOTIPY_REDIRECT_URI")

if not all([SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, SPOTIPY_REDIRECT_URI]):
    logger.error("One or more Spotify environment variables are missing.")
    raise EnvironmentError("Missing Spotify environment variables.")

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

def authorize_url(state: str):
    return sp_oauth.get_authorize_url(state=state)

def get_token(code: str):
    try:
        token_info = sp_oauth.get_access_token(code, as_dict=True)
        return token_info
    except Exception as e:
        logger.error(f"Error obtaining access token: {e}")
        return None

def refresh_token(refresh_token: str):
    try:
        token_info = sp_oauth.refresh_access_token(refresh_token)
        return token_info
    except Exception as e:
        logger.error(f"Error refreshing access token: {e}")
        return None

def fetch_top_albums(token_info: dict, max_albums_per_month: int):
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

def overlay_text_on_image(img: Image.Image, album_name: str, artist_name: str) -> Image.Image:
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

def create_composite_image(albums_by_month: list, max_albums_per_month: int) -> Image.Image:
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

# Shared token storage (Not recommended for production; use a database or secure storage)
# This is a simple in-memory store for demonstration purposes.
user_tokens = {}

def initiate_auth():
    """
    Initiate Spotify OAuth flow by generating a unique state and redirecting the user.
    """
    import uuid
    state = str(uuid.uuid4())
    auth_url = authorize_url(state)
    # Store the state temporarily
    user_tokens[state] = {}
    return f'<a href="{auth_url}" target="_blank">Click here to authorize with Spotify</a>'

def handle_callback(code: str, state: str):
    """
    Handle the OAuth callback by exchanging the authorization code for tokens.
    """
    if state not in user_tokens:
        return "Invalid state parameter. Potential CSRF attack detected."

    token_info = get_token(code)
    if token_info:
        user_tokens[state] = token_info
        return "Authentication successful! You can now close this window and return to the main app."
    else:
        return "Authentication failed. Please try again."

def display_albums(state: str, max_albums_per_month: int):
    """
    Fetch and display top albums, then create and provide a downloadable composite image.
    """
    if state not in user_tokens or not user_tokens[state]:
        return "Not authenticated. Please authorize with Spotify first."

    token_info = user_tokens[state]

    # Check if token is expired
    if sp_oauth.is_token_expired(token_info):
        refreshed_token = refresh_token(token_info['refresh_token'])
        if refreshed_token:
            user_tokens[state] = refreshed_token
            token_info = refreshed_token
        else:
            return "Token refresh failed. Please reauthorize with Spotify."

    top_albums = fetch_top_albums(token_info, max_albums_per_month)

    # Generate HTML to display albums
    html_content = ""
    for month, albums in top_albums.items():
        html_content += f"<h3>{month}</h3><div style='display: flex; flex-wrap: wrap;'>"
        if albums:
            for album in albums[:max_albums_per_month]:
                img = Image.new("RGB", (300, 300), color='gray')  # Placeholder
                if album['image_url']:
                    try:
                        response = requests.get(album['image_url'], timeout=10)
                        response.raise_for_status()
                        img = Image.open(BytesIO(response.content))
                        img = overlay_text_on_image(img, album['name'], album['artist'])
                    except Exception as e:
                        logger.warning(f"Failed to load image for album '{album['name']}': {e}")
                        img = Image.new("RGB", (300, 300), color='gray')
                buf = BytesIO()
                img.save(buf, format="PNG")
                byte_im = buf.getvalue()
                img_base64 = base64.b64encode(byte_im).decode()
                html_content += f'<img src="data:image/png;base64,{img_base64}" style="width:200px;height:200px;margin:5px;">'
        else:
            html_content += "<p>No top albums for this month.</p>"
        html_content += "</div>"

    # Create composite image
    albums_by_month = list(top_albums.items())
    composite_image = create_composite_image(albums_by_month, max_albums_per_month)
    buf = BytesIO()
    composite_image.save(buf, format="PNG")
    byte_im = buf.getvalue()

    # Encode image for download
    import base64
    encoded_image = base64.b64encode(byte_im).decode()

    download_link = f'<a href="data:image/png;base64,{encoded_image}" download="top_albums.png">Download Composite Image</a>'

    return gr.HTML(f"{html_content}<br>{download_link}")

# --------------------------
# Gradio Interface Layout
# --------------------------

with gr.Blocks() as demo:
    gr.Markdown("# Spotify Top Albums of the Year")
    gr.Markdown("Find your top Spotify albums released between December 2023 and December 2024.")
    
    with gr.Tab("Authenticate"):
        gr.Markdown("## Step 1: Authorize with Spotify")
        authorize_btn = gr.Button("Authorize with Spotify")
        authorize_output = gr.HTML()
        authorize_btn.click(fn=initiate_auth, outputs=authorize_output)
    
    with gr.Tab("View Top Albums"):
        gr.Markdown("## Step 2: View Your Top Albums")
        with gr.Row():
            state_input = gr.Textbox(label="Session State", visible=False)
            # The state is managed internally; no need to input manually.
        max_albums_slider = gr.Slider(
            label="How many of your top albums do you want to see per month?",
            minimum=3,
            maximum=10,
            value=5,
            step=1
        )
        view_btn = gr.Button("Fetch and Display Top Albums")
        albums_output = gr.HTML()
        download_output = gr.File()
        view_btn.click(
            fn=lambda: "Functionality not available. Please authenticate first.",
            inputs=None,
            outputs=albums_output
        )
    
    # Note: Handling state securely is complex in this setup.
    # For demonstration, the session state is managed via unique states.

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

    if not code or not state:
        return "Missing code or state parameter."

    # Handle the callback and exchange code for tokens
    token_info = get_token(code)
    if token_info:
        user_tokens[state] = token_info
        # Redirect back to Gradio interface
        return RedirectResponse(url="/")
    else:
        return "Failed to obtain access token."

# --------------------------
# Run Gradio with Uvicorn
# --------------------------

if __name__ == "__main__":
    # Launch Gradio app with FastAPI
    # Start Uvicorn server to run FastAPI and Gradio together
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="error")
