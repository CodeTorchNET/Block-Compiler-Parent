from flask import request, jsonify, Response, stream_with_context
import mysql.connector
import json
import requests
from bs4 import BeautifulSoup
from math import ceil
#import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageFilter
from io import BytesIO
import base64
from datetime import datetime, timedelta
import pytz

from API.services.helpers import get_db_connection , verifyToken, limiter

def check_scratch_user(username):
    """
    Checks if a Scratch user exists.
    """

    if not username:
        return 0

    url = f"https://scratch.mit.edu/users/{username}/"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:127.0) Gecko/20100101 Firefox/127.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Prefer": "safe",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "DNT": "1",
        "Sec-GPC": "1",
        "Priority": "u=1",
        "TE": "trailers"
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        http_code = response.status_code

        if http_code == 404:
            return {"status": "false", "error": "User not found"}
        else:
            return {"status": "true"}

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return {"status": "false", "error": "User not found"}
        else:
            return {"status": "false", "error": f"HTTP Error: {e}"}

    except requests.exceptions.RequestException as e:
        return {"status": "false", "error": f"Request Error: {e}"}
    except Exception as e:
        return {"status": "false", "error": f"Unexpected Error: {e}"}

def verify_scratch_comment(username):
    """
    Verifies a Scratch comment for a specific user, while ensuring it was submitted within the last 30 minutes.
    """

    if not username:
        return json.dumps({"status": "No username provided"})

    url = f"https://scratch.mit.edu/site-api/comments/user/{username}/?page=1"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:127.0) Gecko/20100101 Firefox/127.0",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Prefer": "safe",
        "X-Requested-With": "XMLHttpRequest",
        "Connection": "keep-alive",
        "Referer": f"https://scratch.mit.edu/users/{username}/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "DNT": "1",
        "Sec-GPC": "1"
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        success = False
        soup = BeautifulSoup(response.text, 'html.parser')
        comments = soup.find_all('div', class_='comment')
        for comment in comments:
            user_link = comment.find('a', id='comment-user')
            if user_link:
                comment_username = user_link.get('data-comment-user')
                content_div = comment.find('div', class_='content')
                if content_div:
                    content = content_div.get_text(strip=True)
                    if comment_username.lower() == username.lower() and "CodeTorch Scratch Account Link Verification" in content:
                        time_span = comment.find('span', class_='time')
                        if time_span:
                            timestamp_str = time_span.get('title')
                            if timestamp_str:
                                try:
                                    timestamp = datetime.strptime(timestamp_str, '%Y-%m-%dT%H:%M:%SZ')
                                    timestamp = pytz.utc.localize(timestamp) # Ensure time is UTC
                                    now_utc = datetime.now(pytz.utc)
                                    time_difference = now_utc - timestamp
                                    if time_difference <= timedelta(minutes=30):
                                        success = True
                                        break
                                    else:
                                         return {"status": "false", "error": "Comment is too old, please resubmit a new comment"}
                                except ValueError:
                                    return {"status": "false", "error": "Invalid timestamp format"}
                            else:
                                return {"status": "false", "error": "Timestamp not found"}
                        else:
                            return {"status": "false", "error": "Time element not found"}
        if success:
            return {"status": "true"}
        else:
            return {"status": "false", "error": "Comment not found or verification failed"}

    except requests.exceptions.HTTPError as e:
        return {"status": "false", "error": f"HTTP Error: {e}"}

    except requests.exceptions.RequestException as e:
        return {"status": "false", "error": f"Request Error: {e}"}
    except Exception as e:
        return {"status": "false", "error": f"Unexpected Error: {e}"}    
def get_scratch_projects(username):
    """
    Retrieves a list of Scratch project IDs for a given user.
    """
    if not username:
        return {"status": "false", "error": "No username provided"}

    total_project_length = 0
    all_project_ids = []

    def get_page(username, page):
        nonlocal total_project_length, all_project_ids
        url = f"https://scratch.mit.edu/users/{username}/projects/?page={page}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:127.0) Gecko/20100101 Firefox/127.0",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "Prefer": "safe",
            "X-Requested-With": "XMLHttpRequest",
            "Connection": "keep-alive",
            "Referer": f"https://scratch.mit.edu/users/{username}/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "DNT": "1",
            "Sec-GPC": "1"
        }

        try:
            response = requests.get(url, headers=headers)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            if page == 1:
                box_head = soup.find_all('div', class_='box-head')
                if len(box_head) == 1:
                  total_projects_element = box_head[0].find('h2')
                  if total_projects_element:
                    total_projects_text = total_projects_element.get_text(strip=True)
                    start_index = total_projects_text.find("(") + 1
                    end_index = total_projects_text.find(")")
                    if start_index > 0 and end_index > start_index:
                        total_projects = int(total_projects_text[start_index:end_index])
                        total_project_length = total_projects

            projects = soup.find_all('li', class_='project thumb item')
            for project in projects:
                link = project.find('a')
                if link and 'href' in link.attrs:
                    project_link = link['href']
                    project_id = project_link[len('/projects/'):].rstrip('/')
                    all_project_ids.append(project_id)


        except requests.exceptions.RequestException as e:
            return {"status": "false", "error": f"Request Error: {e}"}
        except Exception as e:
            return {"status": "false", "error": f"Unexpected Error: {e}"}

        return None # Indicate success


    # Main Logic
    error = get_page(username, 1)  #Get first page

    if error:
        return error

    if total_project_length > 60:
        rounds = ceil(total_project_length / 60)
        for i in range(2, rounds + 1):
            error = get_page(username, i)
            if error:
                return error


    return {"status": "true", "project_ids": all_project_ids, "total_project_number": total_project_length}

def downloadProject(projectID):
    """
    Download the project from Scratch using the project ID.
    """
    if not projectID:
        return {"status": "false", "error": "No project ID provided"}
    # --- Headers ---
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:127.0) Gecko/20100101 Firefox/127.0",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Prefer": "safe",
        "X-Requested-With": "XMLHttpRequest",
        "Connection": "keep-alive",
        "Referer": "https://scratch.mit.edu/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "DNT": "1",
        "Sec-GPC": "1"
    }
    # --- First API Call (Project Info) ---
    url = f"https://api.scratch.mit.edu/projects/{projectID}"
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Raise an HTTPError for bad responses
        project_info = response.json()

        projectinstructions = project_info.get('instructions', "")
        if projectinstructions:
            projectinstructions = '<p>' + '</p><p>'.join(projectinstructions.split("\n")) + '</p>'
        else:
            projectinstructions = ""

        projectDisc = project_info.get('description', "")
        if projectDisc:
            projectDisc = '<p>' + '</p><p>'.join(projectDisc.split("\n")) + '</p>'
        else:
            projectDisc = ""


        projectDisc = '<h1>Instructions</h1>' + projectinstructions + '<h1>Notes and Credits</h1>' + projectDisc        #projectDisc = urllib.parse.quote_plus(projectDisc)

        projectTitle = project_info.get('title', "")
        projectThumbnail = project_info.get('image',"")

        projectToken = project_info.get('project_token', "")
        # --- Second API Call (Project Data) ---
        url = f"https://projects.scratch.mit.edu/{projectID}?token={projectToken}"
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Raise an HTTPError for bad responses
        project_data = response.json()

        assetsToDownload = []

        if 'targets' in project_data:
            data = project_data['targets']
            for sprite in data:
                for costume in sprite.get('costumes', []):
                    costume_md5ext = costume.get('md5ext', None)
                    if costume_md5ext:
                        assetsToDownload.append(costume_md5ext)
                for sound in sprite.get('sounds', []):
                    sound_md5ext = sound.get('md5ext', None)
                    if sound_md5ext:
                        assetsToDownload.append(sound_md5ext)
        else:
            # Legacy projects
            if 'sounds' in project_data:
                for sound in project_data['sounds']:
                    sound_md5 = sound.get('md5', None)
                    if sound_md5:
                        assetsToDownload.append(sound_md5)
            if 'costumes' in project_data:
                for costume in project_data['costumes']:
                    costume_baseLayerMD5 = costume.get('baseLayerMD5', None)
                    if costume_baseLayerMD5:
                        assetsToDownload.append(costume_baseLayerMD5)
            for element in project_data.get('children', []):
                if 'costumes' in element:
                    for costume in element['costumes']:
                        costume_baseLayerMD5 = costume.get('baseLayerMD5', None)
                        if costume_baseLayerMD5:
                            assetsToDownload.append(costume_baseLayerMD5)
                if 'sounds' in element:
                    for sound in element['sounds']:
                        sound_md5 = sound.get('md5', None)
                        if sound_md5:
                            assetsToDownload.append(sound_md5)

        # -- Thumbnail Processing --
        # load the image from URL
        thumbnail_url = project_info.get('image', "")
        thumbnail_response = requests.get(thumbnail_url, headers=headers)
        thumbnail_response.raise_for_status()
        image = Image.open(BytesIO(thumbnail_response.content))
                   
        # Resize and Crop
        orig_width, orig_height = image.size
        new_height = 288
        new_width = int((orig_width / orig_height) * new_height)

        resized_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)

        crop_width = 160
        crop_height = 288
        x = (new_width - crop_width) // 2
        y = 0

        cropped_image = resized_image.crop((x, y, x + crop_width, y + crop_height)).convert("RGBA")

        # Apply Gaussian Blur
        for _ in range(100):
            cropped_image = cropped_image.filter(ImageFilter.GaussianBlur(radius=2))

        # Overlay
        overlay_width = 160
        overlay_height = 120
        scale = min(overlay_width / orig_width, overlay_height / orig_height)
        resized_overlay_width = int(orig_width * scale)
        resized_overlay_height = int(orig_height * scale)

        overlay_image = Image.new('RGBA', (overlay_width, overlay_height), (0, 0, 0, 0))
        overlay_image_resized = image.resize((resized_overlay_width, resized_overlay_height), Image.Resampling.LANCZOS)
        overlay_image.paste(overlay_image_resized, ((overlay_width - resized_overlay_width) // 2, (overlay_height - resized_overlay_height) // 2))

        cropped_image.paste(overlay_image, ((crop_width - overlay_width) // 2, (crop_height - overlay_height) // 2), overlay_image) # using overlay_image as mask
    
        # convert image to base64
        buffered = BytesIO()
        cropped_image.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")

        # --- Download Assets ---
        def download_asset(asset):
            """
            downloads a single asset.
            """
            url = f"https://assets.scratch.mit.edu/internalapi/asset/{asset}/get/"
            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                file_path = f"storage/projectData/projectAssets/{asset}" 
                with open(file_path, 'wb') as f:
                    f.write(response.content)
                return (asset, True, None)
            except requests.exceptions.HTTPError as e:
                return (asset, False, str(e))
            except requests.exceptions.RequestException as e:
                return (asset, False, str(e))
            except Exception as e:
                return (asset, False, str(e))

        # -- download 40 assets concurrently --
        success = True
        failed_assets = []

        if assetsToDownload:
            with ThreadPoolExecutor(max_workers=40) as executor:
                results = list(executor.map(download_asset, assetsToDownload))
                for asset, status, error in results:
                    if not status:
                        failed_assets.append({"asset": asset, "error": error})
                        success = False

        # --- Return Project Data ---
        projectData = {
            "status": "true",
            "projectID": projectID,
            "projectTitle": projectTitle,
            "projectThumbnail": projectThumbnail,
            "projectDisc": projectDisc,
            "projectData": project_data,
            "projectImage": img_str,
        }

        if success:
            return projectData
        else:
            #return {"status": "false", "error": "Failed to download all assets.", "failed_assets": failed_assets}
            # attempt to redownload failed assets
            failed_assets_2 = []
            for asset in failed_assets:
                asset_name = asset['asset']
                result = download_asset(asset_name)
                if result[1]:
                    success = True
                else:
                    success = False
                    failed_assets_2.append({"asset": asset_name, "error": result[2]})
            if success:
                return projectData
            else:
                return {"status": "false", "error": "Failed to download all assets after retrying.", "failed_assets": failed_assets_2, "successfully_downloaded": len(failed_assets) - len(failed_assets_2)}



    except requests.exceptions.HTTPError as e:
        return {"status": "false", "error": str(e)}

    except requests.exceptions.RequestException as e:
        return {"status": "false", "error": str(e)}

    except Exception as e:
        return {"status": "false", "error": str(e)}

def insert_new_project(cursor, is_shared, owner, project_title):
    """Inserts a new project into the database and returns the project ID."""
    insert_query = """
    INSERT INTO projects (isShared, Owner, Title)
    VALUES (%s, %s, %s)
    """
    try:
        cursor.execute(insert_query, (is_shared, owner, project_title))
        return cursor.lastrowid
    except mysql.connector.Error as err:
        raise Exception(f"Error inserting new project: {err}")


def save_project_data_to_file(project_id, data):
    """Saves project data to a JSON file."""
    try:
        file_path = f'storage/projectData/projectData/{project_id}.json'
        with open(file_path, 'w') as file:
            file.write(json.dumps(data))
    except FileNotFoundError:
        raise Exception(f"Failed to create project file at {file_path}")

@limiter.limit("10/day")
def internal():
    token = request.json.get('token')
    username = request.json.get('username')
    ScratchUsername = request.json.get('ScratchUsername')
    if(token == None or username == None):
        return jsonify({"status": "error", "message": "Missing token or username"}), 400
    if(ScratchUsername == None):
        return jsonify({"status": "error", "message": "Missing ScratchUsername"}), 400
    # Step 1: Establish DB connection
    try:
        db_connection = get_db_connection()
        cursor = db_connection.cursor()
        if not verifyToken(token, username):
            return jsonify({"status": "error", "message": "Invalid token"}), 403
        scratchUsernameStatus = check_scratch_user(ScratchUsername)
        if(scratchUsernameStatus["status"] == "false"):
            return jsonify({"status": "error", "message": scratchUsernameStatus["error"]}), 400
        commentVerificationStatus = verify_scratch_comment(ScratchUsername)
        if(commentVerificationStatus["status"] == "false"):
            return jsonify({"status": "error", "message": commentVerificationStatus["error"]}), 400
        # We now know that the request is authorized
        def nextStep():
            db_connection = None
            cursor = None
            try:
                # load the users projects
                projects = get_scratch_projects(ScratchUsername)
                if projects['status'] == "false":
                    yield f"data: {json.dumps({'status': 'error', 'step:':'getProjects', 'message': projects['error']})}\n\n"
                    return
                yield f"data: {json.dumps({'status': 'success', 'step':'getProjects', 'message': 'Projects loaded successfully', 'projectCount': projects['total_project_number']})}\n\n"
                db_connection = get_db_connection()
                cursor = db_connection.cursor()
                # downloads the projects
                for projectIDscratch in projects['project_ids']:
                    projectData = downloadProject(projectIDscratch)
                    if projectData['status'] == "false":
                        yield f"data: {json.dumps({'status': 'error', 'step':'downloadProject', 'message': projectData})}\n\n"
                        return
                    else:
                        # actually commit projects to MYSQL
                        projectID = insert_new_project(cursor, 0, username, projectData['projectTitle'])
                        db_connection.commit()
                        if projectID:
                            save_project_data_to_file(projectID, projectData['projectData'])
                            yield f"data: {json.dumps({'status': 'success', 'step':'downloadProject', 'message': 'Project downloaded successfully', 'projectID': projectID, 'projectData': projectData})}\n\n"
                        else:
                            yield f"data: {json.dumps({'status': 'error', 'step':'downloadProject', 'message': 'Failed to insert new project'})}\n\n"
                yield f"data: {json.dumps({'status': 'success', 'step':'completed', 'message': 'All projects downloaded successfully'})}\n\n"
            except mysql.connector.Error as err:
                yield f"data: {json.dumps({'status': 'error', 'step':'databaseError', 'message': f'Database error: {err}'})}\n\n"
            except Exception as err:
                yield f"data: {json.dumps({'status': 'error', 'step':'generalError', 'message': f'General error: {err}'})}\n\n"
            finally:
                if cursor:
                    cursor.close()
                if db_connection:
                    db_connection.close()
        
        return Response(stream_with_context(nextStep()), content_type='text/event-stream')
    except mysql.connector.Error as err:
        return jsonify({"status": "error", "message": f"Database connection error: {err}"}), 500
    finally:
        cursor.close()
        db_connection.close()
def ScratchDownloader():
    if request.method == 'OPTIONS':
        return ""
    if request.method == 'POST':
        return internal()
    else:
        return jsonify({"status": "error", "message": "Invalid request method"}), 405