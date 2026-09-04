def test_root_redirects_to_login_when_not_authenticated(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_page_accessible_without_session(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert b"NUMA FILM ARCHIVE" in response.data
    assert b"numa-film-logo.png" in response.data


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


def test_login_with_unknown_username_shows_the_same_generic_error(client):
    response = client.post("/login", data={"username": "nobody", "password": "testpass"})
    assert response.status_code == 200
    assert b"Invalid username or password" in response.data


def test_login_hashes_even_for_an_unknown_username(client, monkeypatch):
    """No user enumeration: bcrypt must run whether or not the username exists,
    otherwise the response time tells an attacker which usernames are valid."""
    import auth

    calls = []
    real_checkpw = auth.bcrypt.checkpw

    def spy(password, hashed):
        calls.append(hashed)
        return real_checkpw(password, hashed)

    monkeypatch.setattr(auth.bcrypt, "checkpw", spy)

    client.post("/login", data={"username": "nobody", "password": "testpass"})

    assert len(calls) == 1
    assert calls[0] == auth._DUMMY_HASH


def test_logout_clears_session(client):
    client.post("/login", data={"username": "admin", "password": "testpass"})
    client.get("/logout")
    response = client.get("/")
    assert response.status_code == 302
