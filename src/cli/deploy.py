"""
GitHub repository deployment module for creating repositories from templates.
"""

import http.server
import socketserver
import threading
import webbrowser
import httpx
import subprocess
import os
import urllib.parse
from pathlib import Path

REDIRECT_URI = "http://localhost:8080/callback"
ACCESS_TOKEN = None
httpd = None


def _oauth_client_id():
    client_id = os.getenv("GITHUB_CLIENT_ID")
    if not client_id:
        raise RuntimeError(
            "Missing GITHUB_CLIENT_ID. Configure GitHub OAuth credentials before using startup deploy."
        )
    return client_id


def _oauth_client_secret():
    client_secret = os.getenv("GITHUB_CLIENT_SECRET")
    if not client_secret:
        raise RuntimeError(
            "Missing GITHUB_CLIENT_SECRET. Configure GitHub OAuth credentials before using startup deploy."
        )
    return client_secret


class OAuthHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        global ACCESS_TOKEN
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
            return

        code = urllib.parse.parse_qs(parsed.query).get("code", [None])[0]
        if not code:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing code")
            return

        # Exchange code for access token
        try:
            token_res = httpx.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": _oauth_client_id(),
                    "client_secret": _oauth_client_secret(),
                    "code": code,
                    "redirect_uri": REDIRECT_URI,
                },
            )
            token_res.raise_for_status()
            ACCESS_TOKEN = token_res.json().get("access_token")
            print(f"✅ Access token received: {ACCESS_TOKEN}")
        except Exception as e:
            print(f"❌ Error while fetching token: {e}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Token fetch failed")
            return

        # Show success message in browser
        self.send_response(200)
        self.end_headers()
        self.wfile.write(
            "<h1>Login successful! You may close this window.</h1>".encode("utf-8")
        )

        # Stop the callback server
        if httpd is not None:
            threading.Thread(target=httpd.shutdown).start()


def start_callback_server():
    global httpd
    handler = OAuthHandler
    httpd = socketserver.TCPServer(("", 8080), handler)
    print("🌐 Waiting for GitHub callback at http://localhost:8080/callback ...")
    httpd.serve_forever()


def start_oauth():
    auth_url = f"https://github.com/login/oauth/authorize?client_id={_oauth_client_id()}&redirect_uri={REDIRECT_URI}&scope=repo,workflow"
    print("🌀 Opening browser for GitHub login...")
    webbrowser.open(auth_url)


def run_ansible_deploy(
    token,
    repo_name,
    repo_description,
    repo_private,
    template_owner,
    template_repo,
    verbose=False,
):
    """
    Run the Ansible deployment playbook with the given parameters.

    Args:
        token (str): GitHub access token
        repo_name (str): Name of the repository to create
        repo_description (str): Description of the repository
        repo_private (bool): Whether the repository should be private
        template_owner (str): Owner of the template repository
        template_repo (str): Name of the template repository
        verbose (bool): Whether to enable verbose output

    Returns:
        bool: True if deployment was successful, False otherwise
    """
    print("🚀 Running Ansible deployment via uv...")
    env = os.environ.copy()
    env["GITHUB_USER_TOKEN"] = token

    # Look for the playbook file in various locations
    current_file_dir = Path(__file__).resolve().parent
    possible_playbook_paths = [
        Path.cwd() / "playbook.yml",  # Current directory
        Path.cwd()
        / "deployment"
        / "playbook.yml",  # Current directory's deployment subfolder
        current_file_dir / "playbook.yml",  # Same directory as this script
        current_file_dir / ".." / "playbook.yml",  # Parent directory
        current_file_dir / ".." / ".." / "playbook.yml",  # Grandparent directory
        current_file_dir
        / ".."
        / ".."
        / "deployment"
        / "playbook.yml",  # Grandparent's deployment subfolder
    ]

    # Find the first playbook that exists
    playbook_path = None
    for path in possible_playbook_paths:
        if path.exists():
            playbook_path = path
            break

    if not playbook_path:
        # If playbook not found, generate a simple one in the current directory
        playbook_path = Path.cwd() / "playbook.yml"
        generate_default_playbook(playbook_path, verbose)

    # Get the directory containing the playbook
    playbook_dir = playbook_path.parent

    try:
        cmd = [
            "uv",
            "run",
            "ansible-playbook",
            str(playbook_path),
            "--extra-vars",
            f"github_token={token} "
            f"repo_name={repo_name} "
            f"repo_description='{repo_description}' "
            f"repo_private={str(repo_private).lower()} "
            f"template_owner={template_owner} "
            f"template_repo={template_repo}",
        ]

        if verbose:
            print(f"Using playbook at: {playbook_path}")
            print(f"Running command: {' '.join(cmd)}")

        subprocess.run(
            cmd,
            check=True,
            env=env,
            cwd=str(playbook_dir),  # Run from the playbook directory
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Ansible deployment failed: {e}")
        return False


def generate_default_playbook(path, verbose=False):
    """
    Generate a default playbook if none exists.

    Args:
        path (Path): Path where the playbook should be created
        verbose (bool): Whether to enable verbose output
    """
    if verbose:
        print(f"No existing playbook found. Generating a default one at {path}")

    default_playbook = """---
# Default playbook for GitHub repository creation
- name: Create GitHub repository from template
  hosts: localhost
  connection: local
  gather_facts: no
  
  vars:
    github_token: "{{ github_token }}"
    repo_name: "{{ repo_name }}"
    repo_description: "{{ repo_description | default('Created by DeployYourStartup.com') }}"
    repo_private: "{{ repo_private | default('true') }}"
    template_owner: "{{ template_owner | default('Deploy-your-Startup') }}"
    template_repo: "{{ template_repo | default('django-backend-template') }}"
  
  tasks:
    - name: Create GitHub repository from template
      uri:
        url: "https://api.github.com/repos/{{ template_owner }}/{{ template_repo }}/generate"
        method: POST
        headers:
          Authorization: "Bearer {{ github_token }}"
          Accept: "application/vnd.github.v3+json"
        body_format: json
        body:
          name: "{{ repo_name }}"
          description: "{{ repo_description }}"
          private: "{{ repo_private }}"
        status_code: [201]
      register: repo_creation
      
    - name: Show repository creation result
      debug:
        var: repo_creation
        
    - name: Success message
      debug:
        msg: "🎉 Successfully created repository {{ repo_name }} from template {{ template_owner }}/{{ template_repo }}"
"""

    # Create the playbook file
    path.write_text(default_playbook)
    if verbose:
        print("Default playbook generated successfully")


def deploy_github_repo(
    repo_name,
    repo_description,
    repo_private=True,
    template_owner="Deploy-your-Startup",
    template_repo="django-backend-template",
    verbose=False,
):
    """
    Deploy a GitHub repository from a template using the OAuth flow.

    Args:
        repo_name (str): Name of the repository to create
        repo_description (str): Description of the repository
        repo_private (bool): Whether the repository should be private
        template_owner (str): Owner of the template repository
        template_repo (str): Name of the template repository
        verbose (bool): Whether to enable verbose output

    Returns:
        int: Exit code (0 for success, 1 for failure)
    """
    global ACCESS_TOKEN
    ACCESS_TOKEN = None

    try:
        _oauth_client_id()
        _oauth_client_secret()
    except RuntimeError as exc:
        print(f"❌ {exc}")
        return 1

    # 1. Start the callback server in a background thread
    server_thread = threading.Thread(target=start_callback_server)
    server_thread.daemon = True  # Make thread exit when main thread exits
    server_thread.start()

    # 2. Open the GitHub OAuth login page
    start_oauth()

    # 3. Wait until the token has been received
    server_thread.join()

    if ACCESS_TOKEN:
        success = run_ansible_deploy(
            ACCESS_TOKEN,
            repo_name,
            repo_description,
            repo_private,
            template_owner,
            template_repo,
            verbose,
        )
        return 0 if success else 1
    else:
        print("❌ No access token received – exiting.")
        return 1
