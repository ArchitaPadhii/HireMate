// Shared helper functions for all logged-in pages

async function loadUser() {
    const res = await fetch("/api/me");
    if (!res.ok) {
        window.location.href = "/login.html";
        return null;
    }
    const user = await res.json();

    const nameEl = document.getElementById("userName");
    if (nameEl) {
        nameEl.textContent = user.name;
    }
    return user;
}

async function logout() {
    await fetch("/api/logout", { method: "POST" });
    window.location.href = "/";
}