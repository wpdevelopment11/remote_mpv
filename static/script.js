async function send(path, obj) {
    const resp = await fetch(path, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify(obj),
    });
    if (!resp.ok) {
        const data = await resp.json();
        throw Error(data["error"]);
    }
}

async function mpvSetProperty(prop, val) {
    await send("/property", {[prop]: val});
}

async function mpvCommand(cmd, args = []) {
    await send("/command", {cmd, args});
}

async function mpvGetProperty(prop) {
    const resp = await fetch(`/property/${prop}`);
    const data = await resp.json();
    if (!resp.ok) {
        throw Error(data["error"]);
    }
    return data[prop];
}

function showPlaylist(playlist) {
    const currentClass = "playlist-current"
    const table = document.createElement("table");
    table.id = "playlist";
    let current = null;

    function makeCurrent(link) {
        if (current) {
            current.classList.remove(currentClass);
        }
        current = link;
        current.classList.add(currentClass);
    }

    for (let i = 0; i < playlist.length; i++) {
        const item = playlist[i];
        const row = table.insertRow();

        const num_cell = row.insertCell();
        num_cell.appendChild(document.createTextNode(`${i+1}.`));

        const title_cell = row.insertCell();
        const link = document.createElement("a");
        const title = item.title || item.filename;
        link.appendChild(document.createTextNode(title));
        link.href = "#";
        if (item.current) {
            makeCurrent(link);
        }

        title_cell.appendChild(link);

        link.onclick = () => {
            mpvCommand("playlist-play-index", [i]);
            makeCurrent(link);
        };
    }
    document.body.appendChild(table);
}

async function toggleButton(name, onClass, title) {
    function changeTitle(isOn) {
        button.title = isOn ? title[0] : title[1];
    }
    const isOn = await mpvGetProperty(name);
    const button = document.getElementById(name);
    if (isOn) {
        button.classList.add(onClass);
    }
    changeTitle(isOn);
    button.onclick = async () => {
        const isOn = button.classList.toggle(onClass);
        await mpvSetProperty(name, isOn);
        changeTitle(isOn);
    };
}

async function setupSlider(name, getMax) {
    const slider = document.getElementById(name);
    slider.max = await getMax();
    slider.value = await mpvGetProperty(name);
    slider.onchange = async () => {
        await mpvSetProperty(name, slider.value);
    }
}

async function loadState() {
    await toggleButton("mute", "muted", ["Unmute", "Mute"]);
    await toggleButton("pause", "paused", ["Play", "Pause"]);
    await setupSlider("volume", async () => await mpvGetProperty("volume-max"))
}

async function loadPlaylist() {
    const playlist = await mpvGetProperty("playlist");
    showPlaylist(playlist);
}

loadState();
loadPlaylist();
