import base64
import time
import re
import requests
import subprocess
import os
import pickle
import socket
import json

import pychromecast
import netifaces

from email.mime.text import MIMEText
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from datetime import datetime

from media import get_media_info

# --- Config ---
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']

IP_SUBJECT_PATTERN = r"(?i)(re:\s*)?myipqrtc"
AUDIO_SUBJECT_PATTERN = r"(?i)(re:\s*)?audioqrtc"
CAST_SUBJECT_PATTERN = r"(?i)(re:\s*)?castqrtc"
TV_SUBJECT_PATTERN = r"(?i)(re:\s*)?tvqrtc"

POLL_INTERVAL_SECONDS = 30

# Bluetooth speaker/headphone MAC
MY_AUDIO_BT = os.getenv("MY_AUDIO_BT")

MEDIA_BASE_PATH = "/tmp/dtr"
# Fixed video output path
VIDEO_OUTPUT_PATH_BASE = f"{MEDIA_BASE_PATH}/qrtc_video"
# Fixed audio output path
AUDIO_OUTPUT_PATH_BASE = f"{MEDIA_BASE_PATH}/qrtc_audio"
# Fixed image output path
IMAGE_OUTPUT_PATH_BASE = f"{MEDIA_BASE_PATH}/qrtc_image"

# Media server
MEDIA_SERVER_BASE = "http://127.0.0.1:8080"
MEDIA_SERVER_NOTIFY_ENDPOINT = "/ready"

# Optional if running headless/systemd
# os.environ["XDG_RUNTIME_DIR"] = "/run/user/1000"

# --------------

LAUNCH_TIMESTAMP = int(datetime.now().timestamp())
seen_message_ids = set()


def get_public_ip():
    response = requests.get(
        "https://api.ipify.org",
        headers={"Accept": "text/plain"},
        timeout=10
    )
    return response.text.strip()


def get_gmail_service():
    creds = None

    if os.path.exists("token.pickle"):
        with open("token.pickle", "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json",
                SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open("token.pickle", "wb") as f:
            pickle.dump(creds, f)

    return build("gmail", "v1", credentials=creds)


def get_new_emails(service):
    query = f"is:unread after:{LAUNCH_TIMESTAMP}"

    result = service.users().messages().list(
        userId="me",
        q=query
    ).execute()

    messages = result.get("messages", [])

    # Also check sent folder to avoid thread weirdness
    query_sent = (
        f"in:sent after:{LAUNCH_TIMESTAMP} "
        f"(subject:myipqrtc "
        f"OR subject:audioqrtc "
        f"OR subject:tvqrtc) "
        f"OR subject:castqrtc)"
    )

    result_sent = service.users().messages().list(
        userId="me",
        q=query_sent
    ).execute()

    sent = result_sent.get("messages", [])

    all_ids = {m["id"] for m in messages}

    for m in sent:
        if m["id"] not in all_ids:
            messages.append(m)

    return messages


def get_message_details(service, msg_id):
    msg = service.users().messages().get(
        userId="me",
        id=msg_id,
        format="full"
    ).execute()

    headers = {
        h["name"]: h["value"]
        for h in msg["payload"]["headers"]
    }

    return {
        "id": msg_id,
        "thread_id": msg["threadId"],
        "subject": headers.get("Subject", ""),
        "from": headers.get("From", ""),
        "labels": msg.get("labelIds", []),
        "payload": msg["payload"],
        "headers": headers,
    }


def send_reply(service, original, reply_text):
    msg = MIMEText(reply_text)

    msg["to"] = original["from"]
    msg["subject"] = f"Re: {original['subject']}"

    original_message_id = original["headers"].get("Message-ID")

    if original_message_id:
        msg["In-Reply-To"] = original_message_id
        msg["References"] = original_message_id

    raw = base64.urlsafe_b64encode(
        msg.as_bytes()
    ).decode()

    service.users().messages().send(
        userId="me",
        body={
            "raw": raw,
            "threadId": original["thread_id"]
        }
    ).execute()

    print(f"  ↳ Replied to: {original['from']}")


def mark_as_read(service, msg_id):
    try:
        service.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()
    except Exception:
        pass


def is_bt_connected(mac):
    result = subprocess.run(
        ["bluetoothctl", "info", mac],
        capture_output=True,
        text=True
    )

    return "Connected: yes" in result.stdout


def connect_bt(mac):
    print(f"  ↳ Connecting bluetooth device: {mac}")

    result = subprocess.run(
        ["bluetoothctl", "connect", mac],
        capture_output=True,
        text=True,
        timeout=60
    )

    print(result.stdout)

    return (
        "Connection successful" in result.stdout
        or "successful" in result.stdout.lower()
    )


import os
import re
import base64
import requests

from pathlib import Path
from urllib.parse import urlparse, unquote


# ----------------------------
# Extract body from Gmail payload
# ----------------------------

def extract_body(payload):
    body_data = None

    # Direct body
    if payload.get("body", {}).get("data"):
        body_data = payload["body"]["data"]

    # Multipart email
    elif "parts" in payload:
        for part in payload["parts"]:
            mime = part.get("mimeType", "")

            if mime in [
                "text/plain",
                "text/html"
            ]:
                data = (
                    part.get("body", {})
                    .get("data")
                )

                if data:
                    body_data = data
                    break

    if not body_data:
        raise Exception ("  ↳ Could not get any data to parse from the email")

    decoded = (
         base64.urlsafe_b64decode(body_data)
            .decode(errors="ignore")
    )

    return decoded


# ----------------------------
# Download first URL from email
# ----------------------------

def download_url_from_email(
    payload,
    download_dir=MEDIA_BASE_PATH
):
    body = extract_body(payload)

    match = re.search(
        r"https?://[^\s<>\"']+",
        body
    )

    if not match:
        raise Exception ("  ↳ No URL found in email body")
        return None

    url = match.group(0)

    print(f"  ↳ Found URL: {url}")

    # FIXME: Skipping download, just send the URL
    return url

    # ----------------------------
    # Derive filename from URL
    # ----------------------------

    parsed = urlparse(url)

    filename = Path(
        unquote(parsed.path)
    ).name

    if not filename:
        print (" ↳ Warning: could not get the filename of the file to download from the email, using default")
        filename = "downloaded_file"

    os.makedirs(
        download_dir,
        exist_ok=True
    )

    output_path = os.path.join(
        download_dir,
        filename
    )

    # ----------------------------
    # Download file
    # ----------------------------

    response = requests.get(
        url,
        stream=True,
        timeout=(60, 600)
    )

    response.raise_for_status()

    with open(output_path, "wb") as f:
        for chunk in response.iter_content(
            chunk_size=1024 * 1024
        ):
            if chunk:
                f.write(chunk)

    # ----------------------------
    # Verify download
    # ----------------------------

    if not os.path.exists(output_path):
        raise Exception(
            f"No file downloaded at "
            f"{output_path}"
        )

    print(
        f"  ↳ Downloaded URL content to: "
        f"{output_path}"
    )

    return output_path


def download_attachment(
    service,
    msg_id,
    payload,
    download_dir=MEDIA_BASE_PATH
):
    #os.remove(output_path).unlink(missing_ok=True)

    def walk_parts(parts):
        for part in parts:
            filename = part.get("filename")

            if filename:
                attachment_id = part["body"].get(
                    "attachmentId"
                )

                if attachment_id:
                    print(
                        f"  ↳ Downloading attachment: "
                        f"{filename}"
                    )

                    attachment = (
                        service.users()
                        .messages()
                        .attachments()
                        .get(
                            userId="me",
                            messageId=msg_id,
                            id=attachment_id
                        )
                        .execute()
                    )

                    data = attachment["data"]

                    file_data = (
                        base64.urlsafe_b64decode(data)
                    )

                    output_path = os.path.join(
                        download_dir,
                        filename
                    )

                    with open(output_path, "wb") as f:
                        f.write(file_data)

                    print(
                        f"  ↳ Saved attachment to: "
                        f"{output_path}"
                    )

                    return output_path

            if "parts" in part:
                nested = walk_parts(part["parts"])

                if nested:
                    return nested

        return None

    parts = payload.get("parts", [])

    # First try normal Gmail attachment
    result = walk_parts(parts)

    if result:
        print(f"  ↳ Saved media to: {result}")
        return result

    # Fallaback from URL
    result = download_url_from_email(payload) 

    return result


def play_audio(path):
    print(f"  ↳ Playing audio: {path}")

    result = subprocess.run(
        [
            "ffplay",
            "-nodisp",
            "-autoexit",
            "-loglevel",
            "error",
            path
        ],
        capture_output=True,
        text=True
    )

    success = result.returncode == 0

    return success, result.stderr


def get_wlp0_ip():
    interfaces = netifaces.interfaces()

    for iface in interfaces:
        if iface.startswith("wlp0"):
            addrs = netifaces.ifaddresses(iface)

            if netifaces.AF_INET in addrs:
                return addrs[netifaces.AF_INET][0]["addr"]

    raise Exception("No wlp0 interface with IPv4 found")


import subprocess
import json


def cast_stream(info):
    notify_media_server = True

    print(f"  ↳ Casting stream: {info}")

    if "file" in info:
        local_ip = get_wlp0_ip()

        file = Path(info["file"]).name
        url = f"http://{local_ip}:8080/stream/{file}"
    
    elif "url" in info:
        url = info["url"]
        notify_media_server = False

    else:
        raise Exception("ERROR: No url or file in info object")

    print(f"  ↳ Cast URL: {url}")

    cast, browser = get_cast()

    if notify_media_server:
        print("  ↳ Asking media server for playback slot")
    
        response = requests.post(
            (
                MEDIA_SERVER_BASE
                + MEDIA_SERVER_NOTIFY_ENDPOINT
            ),
            json=info,
            timeout=10
        )

        if response.status_code != 200:
            raise Exception( "ERROR: "
                "Media server rejected request "
                "(probably busy)"
            )

        print("  ↳ Media server accepted request")
        
    playback_err = ""

    try:
        # Kill current app/session
        cast.quit_app()

        time.sleep(3)

        mc = cast.media_controller

        mc.play_media(
            url,
            info["mime"]
        )

        mc.block_until_active()

        mc.play()

        print("  ↳ Waiting for playback start")

        playback_started = False

        deadline = time.time() + 300

        while time.time() < deadline:
            mc.update_status()

            state = mc.status.player_state

            print(f"  ↳ Chromecast state: {state}")

            if state == "PLAYING" or (state == "PAUSED" and info.get("mime").startswith("image")):
                playback_started = True
                break

            time.sleep(2)

        if not playback_started:
            playback_err = "ERROR: Chromecast never started playback"
        
        else:
            print("  ↳ Playback started")

            print("  ↳ Waiting for playback completion")

            duration = info.get("duration")
            accum = 0.0;

            if duration and not info.get("mime").startswith("image"):
                # FIXME: if duration is inf, then you risk running this loop forever
                total_wait = duration + 60.0

                while accum < total_wait: 
                    mc.update_status() 

                    state = mc.status.player_state 

                    current_time = mc.status.current_time 
                    duration = mc.status.duration 
                    
                    print( 
                          f" ↳ State={state} " f"time={current_time}/{duration}" 
                    ) 
                    
                    if state in ["IDLE", "UNKNOWN"]: 
                        break 

                    # FIXME: if paused forever, then you risk running this loop forever
                    if state == "PAUSED" or state == "BUFFERING":
                        accum -= 1.8

                    accum += 1.8
                    time.sleep(2) 
            
                if duration and accum >= total_wait:
                    playback_err = "\nERROR: Playback likely never started or tv died or something got stuck - bailing the media server out!"
            
            else:
                # FIXME: Hardcoded 10 s for image??
                time.sleep(10)

            print("  ↳ Playback concluded")
        
        try:
            mc.stop()
            time.sleep(2)
            #cast.quit_app()
        except Exception:
            playback_err += "\nWARNING: Some problem stopping the stream, Nevertheless still notifying the media server to reset "

    except Exception as e:
        playback_err += str(e)

    finally:
        stop_discovery(browser)
        
        if notify_media_server:
            resonse = requests.post(
                (
                    MEDIA_SERVER_BASE
                    + "/playback_finished"
                ),
                timeout=10
            )
       
            print(
                "  ↳ Media server notified "
                "about completion "
                ": State Reset"
            )
        
            if response.status_code != 200:
                playback_err += "\nWARNING: Couldn't inform the media server that the playback is over."
                playback_err += "\nWARNING: Media server state might be undefined."
        
            elif playback_err:
                playback_err += "\nNotified the media server. State reset."
        
        if playback_err:
            raise Exception(playback_err)



def handle_ip_request(service, details):
    ip = get_public_ip()

    print(f"  ↳ Current public IP: {ip}")

    return "Current public IP:\n{ip}"


def handle_audio_request(service, details):
    print("  ↳ Audio request received")

    attachment_path = download_attachment(
        service,
        details["id"],
        details["payload"],
    )
    
    print(f"  ↳ Downloaded attachment: {attachment_path}")
    
    info = get_media_info(attachment_path);
    attachment_path = link_media_file (attachment_path, info["mime"])
    
    print(f"  ↳ Linked to : {attachment_path}")

    if not is_bt_connected(MY_AUDIO_BT):
        print("  ↳ Bluetooth device not connected")

        connected = connect_bt(MY_AUDIO_BT)

        if not connected:
            raise Exception(f"Could not connect to audio bt : {MY_AUDIO_BT}")

        print("  ↳ Bluetooth connected")
    else:
        print("  ↳ Bluetooth already connected")

    time.sleep(5)

    success, error = play_audio(attachment_path)

    if success:
        pass
    else:
        raise Exception(str(error))

def get_cast():
    chromecasts, browser = (
        pychromecast.get_chromecasts()
    )

    if not chromecasts:
        stop_discovery(browser)

        raise Exception(
            "No Chromecast discovered"
        )

    cast = chromecasts[0]

   
    try:
        cast.wait(timeout=10)
        
        friendly_name = (
            getattr(cast, "name", None)
            or getattr(
                getattr(cast, "cast_info", None),
                "friendly_name",
                None
            )
            or "Unknown Chromecast"
        )

    except Exception as e:
        stop_discovery(browser)

        raise e

    if not cast.socket_client.is_connected:
        stop_discovery(browser)
        
        raise Exception(
            "Chromecast not actually reachable"
        )
    
    print(
        f"  ↳ Connected to Chromecast: "
        f"{friendly_name}"
    )

    return cast, browser


def stop_discovery(browser):
    #if browser:
    #    pychromecast.discovery.stop_discovery(browser)
    pass


def handle_tv_request(service, details):
    print("  ↳ TV request received")
    
    cast, browser = get_cast()

    print ("tv request was a success!")

    stop_discovery(browser)

    return "TV is on"
       

def link_media_file(
    input_path,
    mime_type
):
    ext = Path(input_path).suffix.lower()

    if mime_type.startswith("audio/"):
        output_path = (
            f"{AUDIO_OUTPUT_PATH_BASE}{ext}"
        )

    elif mime_type.startswith("video/"):
        output_path = (
            f"{VIDEO_OUTPUT_PATH_BASE}{ext}"
        )
    
    elif mime_type.startswith("image/"):
        output_path = (
            f"{IMAGE_OUTPUT_PATH_BASE}{ext}"
        )

    else:
        os.unlink(input_path)
        raise ValueError(
            f"Unsupported mime type: {mime_type}"
        )

    # Remove existing file if present
    try:
        os.unlink(output_path)
    except FileNotFoundError:
        pass

    os.link(input_path, output_path)
    os.unlink(input_path)

    return output_path


def handle_cast_request(service, details):
    print("  ↳ Cast request received")

    output_path = download_attachment(
        service,
        details["id"],
        details["payload"]
    )
        
    info = get_media_info(output_path);
        
    if re.match(r'https?://', output_path)
        info["url"] = output_path 

    else:
        info["file"] = link_media_file (output_path, info["mime"])

    cast_stream(info)

def poll(service):
    print(f"Listening since: {datetime.fromtimestamp(LAUNCH_TIMESTAMP)}")
    print(f"IP subject pattern: '{IP_SUBJECT_PATTERN}'")
    print(f"Audio subject pattern: '{AUDIO_SUBJECT_PATTERN}'")
    print(f"Cast subject pattern: '{CAST_SUBJECT_PATTERN}'")
    print(f"TV subject pattern: '{TV_SUBJECT_PATTERN}'")

    while True:
        try:
            emails = get_new_emails(service)

            new_emails = [
                e for e in emails
                if e["id"] not in seen_message_ids
            ]

            if new_emails:
                print(
                    f"[{time.strftime('%H:%M:%S')}] "
                    f"{len(new_emails)} new email(s)"
                )

            for email in new_emails:
                seen_message_ids.add(email["id"])

                details = get_message_details(
                    service,
                    email["id"]
                )

                subject = details["subject"]
                
                #labels = details["labels"]
                # Ignore our own sent-only emails
                #if "SENT" in labels and "INBOX" not in labels:
                #    print ("ignoring")
                #    continue

                print(
                    f"Checking subject: '{subject}' "
                    f"from {details['from']}"
                )

                if re.search(IP_SUBJECT_PATTERN, subject):
                    print("  ✓ IP request matched")

                    try:
                        ret = handle_ip_request(service, details)
                        send_reply (service, details, "SUCCESS: " + ret) 
                    
                    except Exception as e:
                        send_reply (service, details, "FAILURE: " + str(e)) 

                    finally:
                        mark_as_read(service, email["id"])

                elif re.search(AUDIO_SUBJECT_PATTERN, subject):
                    print("  ✓ Audio request matched")

                    try:
                        handle_audio_request(service, details)
                        send_reply (service, details, "SUCCESS") 

                    except Exception as e:
                        send_reply (service, details, "FAILURE: " + str(e)) 

                    finally:
                        mark_as_read(service, email["id"])

                elif re.search(CAST_SUBJECT_PATTERN, subject):
                    print("  ✓ Cast request matched")

                    try:
                        handle_cast_request(service, details)
                        send_reply (service, details, "SUCCESS") 
                    
                    except Exception as e:
                        send_reply (service, details, "FAILURE: " + str(e)) 

                    finally:
                        mark_as_read(service, email["id"])
                
                elif re.search(TV_SUBJECT_PATTERN, subject):
                    print("  ✓ tv request matched")

                    try:
                        ret = handle_tv_request(service, details)
                        send_reply (service, details, "SUCCESS: " + ret) 
                    
                    except Exception as e:
                        send_reply (service, details, "FAILURE " + str(e)) 

                    finally:
                        mark_as_read(service, email["id"])

                else:
                    print(f"  ✗ Skip: '{subject}'")

        except Exception as e:
            print(f"ERROR: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    service = get_gmail_service()
    poll(service)
