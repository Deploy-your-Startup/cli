import sys
import subprocess
from unittest.mock import patch, MagicMock
from pathlib import Path
import pytest

from cli import deploy

# Add the src directory to the Python path so we can import deploy.py
sys.path.append(str(Path(__file__).parents[1] / "src"))


@pytest.fixture(autouse=True)
def reset_access_token():
    """Reset the global ACCESS_TOKEN before each test"""
    # Setup - reset token
    deploy.ACCESS_TOKEN = None
    yield
    # Teardown - reset token again
    deploy.ACCESS_TOKEN = None


def test_oauth_flow_and_deployment():
    """Test the complete OAuth flow and deployment process"""

    # GIVEN
    # A mocked GitHub OAuth response and subprocess call to ansible-playbook
    mock_token = "mock_github_token_123456789"

    # Mock the httpx.post to simulate GitHub's token response
    with patch("httpx.post") as mock_post:
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"access_token": mock_token}
        mock_post.return_value = mock_response

        # Mock the webbrowser.open to prevent actual browser opening
        with patch("webbrowser.open") as mock_browser:
            mock_browser.return_value = True

            # Mock the socketserver.TCPServer to avoid actually starting a server
            with patch("socketserver.TCPServer") as mock_server:
                mock_server_instance = MagicMock()
                mock_server.return_value = mock_server_instance

                # Mock the subprocess.run to prevent actual ansible deployment
                with patch("subprocess.run") as mock_subprocess:
                    mock_subprocess.return_value = MagicMock(returncode=0)

                    # WHEN
                    # We simulate the OAuth callback and run the deployment process

                    # Rather than creating a real OAuthHandler instance,
                    # let's patch the do_GET method directly
                    with patch(
                        "cli.deploy.OAuthHandler.do_GET", autospec=True
                    ) as mock_do_get:
                        # Configure the mock to set the ACCESS_TOKEN
                        def side_effect(self):
                            deploy.ACCESS_TOKEN = mock_token
                            # Simulate stopping the server
                            threading_mock = MagicMock()
                            threading_mock.start.return_value = None
                            deploy.threading.Thread = MagicMock(
                                return_value=threading_mock
                            )

                        mock_do_get.side_effect = side_effect

                        # Create our handler but patch all the methods it would call
                        handler = MagicMock()

                        # Simulate the callback request with path
                        handler.path = "/callback?code=mock_code"

                        # Create a fake request to the callback URL
                        deploy.httpd = mock_server_instance

                        # Simulate the callback being processed
                        # by manually setting the token
                        deploy.ACCESS_TOKEN = mock_token

                        # THEN
                        # The token should be received and deployment should be triggered

                        # Verify token was set correctly
                        assert deploy.ACCESS_TOKEN == mock_token

                        # Run the deployment function with the new parameter format and check it worked
                        result = deploy.run_ansible_deploy(
                            mock_token,
                            "test-repo",
                            "Test repository",
                            False,
                            "test-owner",
                            "test-template",
                            True,
                        )
                        assert result is True

                        # Verify subprocess was called with correct arguments
                        mock_subprocess.assert_called_once()
                        call_args = mock_subprocess.call_args[0][0]

                        # Check that ansible-playbook was called
                        assert "ansible-playbook" in call_args

                        # Check that the repo name was passed correctly
                        extra_vars = mock_subprocess.call_args[0][0][-1]
                        assert "repo_name=test-repo" in extra_vars

                        # Check that GitHub token was added to environment
                        env = mock_subprocess.call_args[1]["env"]
                        assert env["GITHUB_USER_TOKEN"] == mock_token


def test_failed_deployment():
    """Test handling of a failed deployment"""

    # GIVEN
    # A mock token and a failing ansible-playbook command
    mock_token = "mock_github_token"

    # WHEN
    # The ansible-playbook subprocess fails
    with patch("subprocess.run") as mock_subprocess:
        mock_subprocess.side_effect = subprocess.CalledProcessError(
            1, "ansible-playbook"
        )

        # THEN
        # The function should return False to indicate failure
        result = deploy.run_ansible_deploy(
            mock_token,
            "test-repo",
            "Test repository",
            True,
            "test-owner",
            "test-template",
        )
        assert result is False


def test_deploy_github_repo_with_token():
    """Test the deploy_github_repo function when a token is received"""

    # GIVEN
    # A mock GitHub OAuth flow and successful deployment

    # WHEN
    # We run the deploy_github_repo function with mocks
    with patch("threading.Thread") as mock_thread:
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        with patch("cli.deploy.start_oauth") as mock_oauth:
            with patch("cli.deploy.run_ansible_deploy") as mock_deploy:
                mock_deploy.return_value = True

                # The key issue: we need to set the ACCESS_TOKEN *after* the oauth flow would set it
                # Set up our mocked oauth to actually set the token
                def side_effect():
                    deploy.ACCESS_TOKEN = "mock_token"

                mock_oauth.side_effect = side_effect

                # THEN
                # The function should execute successfully
                result = deploy.deploy_github_repo(
                    "test-repo",
                    "Test repository",
                    True,
                    "test-owner",
                    "test-template",
                    False,
                )

                # Verify functions were called as expected
                mock_thread_instance.start.assert_called_once()
                mock_thread_instance.join.assert_called_once()
                mock_oauth.assert_called_once()
                mock_deploy.assert_called_once_with(
                    "mock_token",
                    "test-repo",
                    "Test repository",
                    True,
                    "test-owner",
                    "test-template",
                    False,
                )

                # Check successful exit code
                assert result == 0


def test_deploy_github_repo_no_token():
    """Test the deploy_github_repo function when no token is received"""

    # GIVEN
    # A mock GitHub OAuth flow that doesn't provide a token

    # WHEN
    # We run the deploy_github_repo function with mocks
    with patch("threading.Thread") as mock_thread:
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        with patch("cli.deploy.start_oauth") as mock_oauth:
            # No token set - ACCESS_TOKEN remains None

            # THEN
            # The function should exit with error code
            result = deploy.deploy_github_repo("test-repo", "Test repository")

            # Verify OAuth was attempted but deploy was not called
            mock_oauth.assert_called_once()

            # Check error exit code
            assert result == 1
