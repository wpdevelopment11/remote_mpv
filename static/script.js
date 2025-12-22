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
        throw Error(data.error);
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
        throw Error(data.error);
    }
    return data[prop];
}

async function mpvEvent() {
    const props = {
        "duration": 0,
        "mute": false,
        "pause": false,
        "playlist": [],
        "time_pos": 0,
        "playlistloaded": false,
        "track-list": [],
        "volume": 100,
        "volume-max": 130,
    };

    (function update_time_pos() {
        setTimeout(async () => {
            let time_pos;
            try {
                time_pos = await mpvGetProperty("time-pos");
            } catch(e) {
                update_time_pos();
                throw e;
            }

            props.time_pos = time_pos;
            setupSeekBar({props, events: []});

            update_time_pos();
        }, 500);
    })();

    for (;;) {
        try {
            const resp = await fetch("/event");
            if (!resp.ok) {
                throw Error("Error while trying to get events");
            }

            const reader = resp.body.getReader();
            let buff = [];
            while (true) {
                const res = await reader.read();
                if (res.done) {
                    throw Error("Unexpected EOF");
                }
                buff.push(...res.value)
                if (res.value[res.value.length-1] == 10) {
                    const decoder = new TextDecoder();
                    let messages = decoder.decode(Uint8Array.from(buff));
                    messages = messages.split("\n").slice(0, -1).map(message => JSON.parse(message));
                    const events = [];
                    for (const message of messages) {
                        if (message.event == "property-change" && message.data != undefined) {
                            props[message.name] = message.data;
                        } else {
                            events.push(message);
                        }
                    }
                    updateState({props, events});

                    buff = [];
                }
            }
        } catch(e) {
            console.log(e);
            await new Promise(resolve => setTimeout(resolve, 3000));
        }
    }
}

function showPlaylist(state) {
    const id = "playlist";
    const playlist = state.props.playlist;
    const currentClass = "playlist-current"
    const table = document.getElementById(id);
    let current;
    let currentId;

    if (!playlist.length) {
        return;
    }

    function makeCurrent(id) {
            current.classList.remove(currentClass);
        current = links[id];
        currentId = id;
        current.classList.add(currentClass);
        document.location.hash = current.hash;
    }

    const prev_button = document.getElementById("playlist-prev");
    prev_button.onclick = () => {
        currentId = (currentId + playlist.length - 1) % playlist.length;
        makeCurrent(currentId);
        mpvCommand("playlist-play-index", [currentId]);
    }

    const next_button = document.getElementById("playlist-next");
    next_button.onclick = () => {
        currentId = (currentId + 1) % playlist.length;
        makeCurrent(currentId);
        mpvCommand("playlist-play-index", [currentId]);
    }

    table.replaceChildren();
    for (let i = 0; i < playlist.length; i++) {
        const item = playlist[i];
        const row = table.insertRow();

        const num_cell = row.insertCell();
        num_cell.appendChild(document.createTextNode(`${i+1}.`));

        const title_cell = row.insertCell();
        const link = document.createElement("a");
        const title = item.title || item.filename;
        link.appendChild(document.createTextNode(title));
        link.href = "";
        link.hash = item.filename;
        if (item.current) {
            currentId = i;
            current = link;
            current.classList.add(currentClass);
        }

        title_cell.appendChild(link);

        link.onclick = () => {
            makeCurrent(i);
            mpvCommand("playlist-play-index", [i]);
        };
    }

    const links = table.querySelectorAll("a");

    const props = state.props;

    if (!props.playlistloaded) {
        const filename = decodeURI(document.location.hash).slice(1);
        for (let i = 0; i < playlist.length; i++) {
            const entry = playlist[i];
            if (entry.filename == filename && i != currentId) {
                makeCurrent(i);
                mpvCommand("playlist-play-index", [currentId]);
                break;
            }
        }
    }

    document.location.hash = current.hash;
    props.playlistloaded = true;
}

function showTracks(state, type) {
    const id = `${type}-tracks`;
    const select = document.getElementById(id);
    select.replaceChildren();

    select.onchange = async (event) => {
        let option;
        if (type == "audio") {
            option = "aid";
        } else if (type == "sub") {
            option = "sid";
        } else {
            throw Error(`Unexpected track type: ${type}`);
        }
        await mpvSetProperty(option, event.target.value);
    };

    const placeholder = document.createElement("option");
    placeholder.value = "no";
    const typeName = type == "sub" ? "subtitle" : type;
    placeholder.text = `Select ${typeName} track:`;
    placeholder.selected = true;
    select.add(placeholder);

    for (const track of state.props["track-list"]) {
        if (track.type != type) continue;

        const trackopt = document.createElement("option");
        trackopt.value = track.id;

        trackopt.text = `${track.id}:`;
        trackopt.text += track.lang ? ` [${track.lang}]` : "";
        trackopt.text += track.title ? ` ${track.title}` : " No title";

        if (track.selected) {
            trackopt.selected = true;
            placeholder.selected = false;
        }
        select.add(trackopt);
    }
}

function toggleButton(state, name, onClass, title) {
    function changeTitle(isOn) {
        button.title = isOn ? title[0] : title[1];
    }
    const isOn = state.props[name];
    const button = document.getElementById(name);
    if (isOn) {
        button.classList.add(onClass);
    } else {
        button.classList.remove(onClass);
    }
    changeTitle(isOn);
    button.onclick = async () => {
        const isOn = button.classList.toggle(onClass);
        changeTitle(isOn);
        await mpvSetProperty(name, isOn);
    };
}

function setupSlider(state, name, maxName) {
    const slider = document.getElementById(name);
    slider.value = state.props[name];
    slider.max = state.props[maxName];
    slider.onchange = async () => {
        await mpvSetProperty(name, slider.value);
    }
}

function setupSeekBar(state) {
    const seekbar = document.getElementById("seek");
    seekbar.value = state.props.time_pos;
    seekbar.max = state.props.duration;
    seekbar.oninput = async () => {
        state.props.time_pos = seekbar.valueAsNumber;
        await mpvCommand("seek", [seekbar.valueAsNumber, "absolute+exact"]);
    }
}

function updateState(state) {
    toggleButton(state, "mute", "muted", ["Unmute", "Mute"]);
    toggleButton(state, "pause", "paused", ["Play", "Pause"]);
    showPlaylist(state);
    setupSlider(state, "volume", "volume-max");
    setupSeekBar(state);
    showTracks(state, "audio");
    showTracks(state, "sub");
}

document.getElementById("volume-decr").onclick = () => mpvCommand("add", ["volume", -10]);
document.getElementById("volume-incr").onclick = () => mpvCommand("add", ["volume", 10]);

document.getElementById("speed-decr").onclick = () => mpvCommand("multiply", ["speed", 1/1.1]);
document.getElementById("speed-incr").onclick = () => mpvCommand("multiply", ["speed", 1.1]);

document.querySelectorAll(".seek").forEach((el) => {
    el.onclick = () => mpvCommand("seek", [parseFloat(el.getAttribute("data-seek"))])
});

mpvEvent();
