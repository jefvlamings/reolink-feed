class ReolinkFeedCard extends HTMLElement {
  constructor() {
    super();
    this._config = null;
    this._hass = null;
    this._items = [];
    this._filteredItems = [];
    this._error = null;
    this._loading = false;
    this._refreshTimer = null;
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    this._config = {
      title: "Reolink Feed",
      since_hours: 24,
      limit: 200,
      labels: ["person", "animal"],
      cameras: [],
      refresh_seconds: 20,
      ...config,
    };
    this._scheduleRefresh();
    this._render();
    this._loadItems();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._items.length && !this._loading) {
      this._loadItems();
    } else {
      this._render();
    }
  }

  disconnectedCallback() {
    if (this._refreshTimer) {
      clearInterval(this._refreshTimer);
      this._refreshTimer = null;
    }
  }

  getCardSize() {
    return 6;
  }

  _scheduleRefresh() {
    if (this._refreshTimer) {
      clearInterval(this._refreshTimer);
    }
    if (!this._config || !this._config.refresh_seconds) {
      return;
    }
    this._refreshTimer = setInterval(
      () => this._loadItems(),
      Math.max(5, this._config.refresh_seconds) * 1000
    );
  }

  _applyFilters() {
    const cameraFilter = new Set((this._config?.cameras || []).map((v) => String(v).toLowerCase()));
    this._filteredItems = this._items.filter((item) => {
      if (!cameraFilter.size) return true;
      return cameraFilter.has(String(item.camera_name || "").toLowerCase());
    });
  }

  async _loadItems() {
    if (!this._hass || !this._config) {
      return;
    }
    this._loading = true;
    this._error = null;
    this._render();
    try {
      const result = await this._hass.callWS({
        type: "reolink_feed/list",
        since_hours: this._config.since_hours,
        limit: this._config.limit,
        labels: this._config.labels,
      });
      this._items = result.items || [];
      this._applyFilters();
    } catch (err) {
      this._error = err?.message || String(err);
    } finally {
      this._loading = false;
      this._render();
    }
  }

  async _openRecording(item) {
    if (!this._hass) {
      return;
    }
    let recording = item.recording || { status: "pending" };
    if (recording.status !== "linked") {
      try {
        recording = await this._hass.callWS({
          type: "reolink_feed/resolve_recording",
          id: item.id,
        });
      } catch (err) {
        this._showToast(`Resolve failed: ${err?.message || err}`);
        return;
      }
      item.recording = recording;
      this._render();
    }

    if (recording.status === "linked" && recording.media_content_id) {
      const url = `/media-browser/browser?media_source_id=${encodeURIComponent(recording.media_content_id)}`;
      window.open(url, "_blank", "noopener");
      return;
    }
    this._showToast("Clip not ready yet");
  }

  _showToast(message) {
    const event = new Event("hass-notification", {
      bubbles: true,
      composed: true,
    });
    event.detail = { message };
    this.dispatchEvent(event);
  }

  _formatTime(ts) {
    if (!ts) return "";
    return new Date(ts).toLocaleString();
  }

  _formatDuration(totalSeconds) {
    if (totalSeconds === null || totalSeconds === undefined) return "-";
    const seconds = Math.max(0, Number(totalSeconds) || 0);
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) return `${h}h ${m}m ${s}s`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  }

  _render() {
    if (!this.shadowRoot || !this._config) {
      return;
    }

    const title = this._config.title || "Reolink Feed";
    const listHtml = this._filteredItems
      .map((item) => {
        const status = item.recording?.status || "pending";
        const action = status === "linked" ? "open clip" : "resolve";
        const statusText = status === "linked" ? "linked" : status === "not_found" ? "not found" : "pending";
        const image = item.snapshot_url
          ? `<img src="${item.snapshot_url}" alt="${item.camera_name}" loading="lazy" />`
          : `<div class="placeholder">No snapshot</div>`;

        return `
          <li class="item" data-id="${item.id}" role="button" tabindex="0">
            <div class="thumb">${image}</div>
            <div class="meta">
              <div class="line1">
                <span class="camera">${item.camera_name}</span>
                <span class="label ${item.label}">${item.label}</span>
              </div>
              <div class="line2">${this._formatTime(item.start_ts)}</div>
              <div class="line3">
                <span>${this._formatDuration(item.duration_s)}</span>
                <span class="status ${status}">${statusText}</span>
              </div>
            </div>
            <button class="open">${action}</button>
          </li>
        `;
      })
      .join("");

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 10px; }
        .head { font-weight: 600; margin-bottom: 8px; }
        .state { color: var(--secondary-text-color); font-size: 12px; margin-bottom: 8px; }
        ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
        .item { display: grid; grid-template-columns: 110px 1fr auto; gap: 10px; align-items: center; border: 1px solid var(--divider-color); border-radius: 10px; padding: 8px; cursor: pointer; }
        .item:hover { background: var(--secondary-background-color); }
        .thumb { width: 110px; height: 62px; overflow: hidden; border-radius: 8px; background: #111; }
        .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
        .placeholder { color: #ddd; font-size: 11px; padding: 8px; }
        .line1 { display: flex; gap: 8px; align-items: center; font-size: 13px; }
        .camera { font-weight: 600; }
        .label { text-transform: uppercase; font-size: 10px; padding: 2px 6px; border-radius: 999px; letter-spacing: 0.04em; }
        .label.person { background: #1d3557; color: #fff; }
        .label.animal { background: #2a9d8f; color: #fff; }
        .line2, .line3 { color: var(--secondary-text-color); font-size: 12px; margin-top: 2px; }
        .line3 { display: flex; justify-content: space-between; }
        .status { padding: 1px 6px; border-radius: 999px; border: 1px solid var(--divider-color); text-transform: lowercase; }
        .status.linked { color: #0b6b3a; border-color: #0b6b3a55; }
        .status.pending { color: #8a6500; border-color: #8a650055; }
        .status.not_found { color: #8b1e1e; border-color: #8b1e1e55; }
        button.open { border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); border-radius: 8px; padding: 6px 8px; cursor: pointer; text-transform: lowercase; }
        button.open:hover { background: var(--secondary-background-color); }
        .empty { color: var(--secondary-text-color); font-size: 13px; padding: 8px 2px; }
        .error { color: var(--error-color); font-size: 12px; white-space: pre-wrap; }
      </style>
      <ha-card>
        <div class="head">${title}</div>
        <div class="state">${this._loading ? "loading..." : `${this._filteredItems.length} items`}</div>
        ${this._error ? `<div class="error">${this._error}</div>` : ""}
        ${this._filteredItems.length ? `<ul>${listHtml}</ul>` : `<div class="empty">No detections in range.</div>`}
      </ha-card>
    `;

    this.shadowRoot.querySelectorAll("li.item").forEach((el) => {
      const id = el.getAttribute("data-id");
      const item = this._items.find((x) => x.id === id);
      const button = el.querySelector("button.open");

      if (item) {
        el.addEventListener("click", () => this._openRecording(item));
        el.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            this._openRecording(item);
          }
        });
      }

      if (button && item) {
        button.addEventListener("click", (ev) => {
          ev.stopPropagation();
          this._openRecording(item);
        });
      }
    });
  }
}

if (!customElements.get("reolink-feed-card")) {
  customElements.define("reolink-feed-card", ReolinkFeedCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "reolink-feed-card",
  name: "Reolink Feed Card",
  description: "Timeline of Reolink person/animal detections",
});
