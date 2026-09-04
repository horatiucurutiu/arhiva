def test_root_redirects_to_login_when_not_authenticated(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_page_accessible_without_session(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert b"NUMA FILM ARCHIVE" in response.data


def test_login_with_correct_credentials_grants_access(client):
    response = client.post(
        "/login",
        data={"username": "admin", "password": "testpass"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert response.request.path == "/"


def test_login_with_wrong_password_shows_error(client):
    response = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert response.status_code == 200
    assert b"Invalid username or password" in response.data


def test_logout_clears_session(client):
    client.post("/login", data={"username": "admin", "password": "testpass"})
    client.get("/logout")
    response = client.get("/")
    assert response.status_code == 302
