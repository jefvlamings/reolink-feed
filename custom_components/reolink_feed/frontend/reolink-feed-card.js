const SUPPORTED_CARD_LABELS = ["person", "pet", "vehicle", "motion", "visitor"];
const LEGACY_CARD_LABEL_ALIASES = { animal: "pet" };

function normalizeCardLabel(label) {
  const lowered = String(label || "").toLowerCase().trim();
  return LEGACY_CARD_LABEL_ALIASES[lowered] || lowered;
}

const CARD_I18N = {
  en: {
    person: "Person",
    pet: "Pet",
    vehicle: "Vehicle",
    motion: "Motion",
    visitor: "Visitor",
    detection_info: "Detection info",
    no_snapshot: "No snapshot",
    camera: "Camera",
    timestamp: "Timestamp",
    duration: "Duration",
    detection: "Detection",
    history: "History",
    logbook: "Logbook",
    file: "File",
    go_to_folder: "Go to folder",
    reset: "Reset",
    delete: "Delete",
    no_detections: "No detections in range.",
    previous: "Previous",
    next: "Next",
    page: "Page",
    open_recording_preview: "Open recording preview",
    show_detection_info: "Show detection info",
    recording_preview: "Recording preview",
    close: "Close",
    close_info_dialog: "Close info dialog",
    snapshot: "Snapshot",
    page_size: "Items per page",
  },
  nl: {
    person: "Persoon",
    pet: "Huisdier",
    vehicle: "Voertuig",
    motion: "Beweging",
    visitor: "Bezoeker",
    detection_info: "Detectie-info",
    no_snapshot: "Geen snapshot",
    camera: "Camera",
    timestamp: "Tijdstip",
    duration: "Duur",
    detection: "Detectie",
    history: "Geschiedenis",
    logbook: "Logboek",
    file: "Bestand",
    go_to_folder: "Ga naar map",
    reset: "Reset",
    delete: "Verwijderen",
    no_detections: "Geen detecties in bereik.",
    previous: "Vorige",
    next: "Volgende",
    page: "Pagina",
    open_recording_preview: "Open opnamevoorbeeld",
    show_detection_info: "Toon detectie-info",
    recording_preview: "Opnamevoorbeeld",
    close: "Sluiten",
    close_info_dialog: "Sluit detectiedialoog",
    snapshot: "Snapshot",
    page_size: "Items per pagina",
  },
};

class ReolinkFeedCard extends HTMLElement {
  constructor() {
    super();
    this._config = null;
    this._hass = null;
    this._items = [];
    this._filteredItems = [];
    this._error = null;
    this._loading = false;
    this._resolvingIds = new Set();
    this._page = 1;
    this._availableLabels = [...SUPPORTED_CARD_LABELS];
    this._activeLabels = new Set();
    this._configuredLabels = [];
    this._filtersInitialized = false;
    this._infoDialog = { open: false, itemId: "" };
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    this._config = {
      labels: [],
      cameras: [],
      per_entity_changes: 400,
      page_size: 20,
      ...config,
    };
    const rawLabels = Array.isArray(this._config.labels)
      ? this._config.labels.map((label) => String(label || "").toLowerCase().trim())
      : [];
    // Migrate legacy card default (person + animal) to "all labels enabled by default".
    const isLegacyDefaultLabels =
      rawLabels.length === 2 && rawLabels.includes("person") && rawLabels.includes("animal");
    const configuredLabels = isLegacyDefaultLabels
      ? []
      : rawLabels
          .map((label) => normalizeCardLabel(label))
          .filter((label) => SUPPORTED_CARD_LABELS.includes(label));
    this._configuredLabels = configuredLabels;
    this._activeLabels = new Set(configuredLabels);
    this._filtersInitialized = false;
    this._render();
    this._loadItems();
  }

  static async getConfigElement() {
    return document.createElement("reolink-feed-card-editor");
  }

  static getStubConfig() {
    return {
      type: "custom:reolink-feed-card",
    };
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._items.length && !this._loading) {
      this._loadItems();
    }
  }

  getCardSize() {
    return 6;
  }

  _languageCode() {
    const raw = String(this._hass?.language || document.documentElement.lang || "en").toLowerCase();
    if (CARD_I18N[raw]) return raw;
    const base = raw.split("-")[0];
    if (CARD_I18N[base]) return base;
    return "en";
  }

  _t(key) {
    const lang = this._languageCode();
    return CARD_I18N[lang]?.[key] || CARD_I18N.en[key] || key;
  }

  _labelText(label) {
    const normalized = normalizeCardLabel(label);
    if (SUPPORTED_CARD_LABELS.includes(normalized)) {
      return this._t(normalized);
    }
    return normalized ? normalized.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "-";
  }

  _labelIconName(label) {
    const normalized = normalizeCardLabel(label);
    if (normalized === "pet") return "mdi:dog-side";
    if (normalized === "vehicle") return "mdi:car";
    if (normalized === "motion") return "mdi:motion-sensor";
    if (normalized === "visitor") return "mdi:doorbell";
    return "mdi:account";
  }

  _applyFilters() {
    const cameraFilter = new Set((this._config?.cameras || []).map((v) => String(v).toLowerCase()));
    this._filteredItems = this._items.filter((item) => {
      const label = normalizeCardLabel(item.label);
      if (!this._activeLabels.has(label)) return false;
      if (!cameraFilter.size) return true;
      return cameraFilter.has(String(item.camera_name || "").toLowerCase());
    });
    this._page = Math.min(this._page, this._totalPages());
    if (this._page < 1) this._page = 1;
  }

  _pageSize() {
    const raw = Number(this._config?.page_size ?? 20);
    if (!Number.isFinite(raw)) return 20;
    return Math.max(1, Math.min(100, Math.floor(raw)));
  }

  _totalPages() {
    return Math.max(1, Math.ceil(this._filteredItems.length / this._pageSize()));
  }

  _pagedItems() {
    const pageSize = this._pageSize();
    const page = Math.max(1, Math.min(this._page, this._totalPages()));
    const start = (page - 1) * pageSize;
    return this._filteredItems.slice(start, start + pageSize);
  }

  _toggleLabelFilter(label) {
    if (this._activeLabels.has(label)) {
      this._activeLabels.delete(label);
    } else {
      this._activeLabels.add(label);
    }
    this._applyFilters();
    this._render();
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
      });
      const enabledLabels = Array.isArray(result?.enabled_labels)
        ? result.enabled_labels
            .map((label) => normalizeCardLabel(label))
            .filter((label) => SUPPORTED_CARD_LABELS.includes(label))
        : [...SUPPORTED_CARD_LABELS];
      this._availableLabels = [...new Set(enabledLabels)];
      if (!this._filtersInitialized) {
        const initial =
          this._configuredLabels.length > 0
            ? this._configuredLabels.filter((label) => this._availableLabels.includes(label))
            : [...this._availableLabels];
        this._activeLabels = new Set(initial);
        this._filtersInitialized = true;
      } else {
        const allowed = new Set(this._availableLabels);
        this._activeLabels = new Set([...this._activeLabels].filter((label) => allowed.has(label)));
      }
      this._items = (result.items || []).map((item) => ({
        ...item,
        label: normalizeCardLabel(item.label),
      }));
      this._applyFilters();
    } catch (err) {
      this._error = err?.message || String(err);
    } finally {
      this._loading = false;
      this._render();
    }
  }

  async _refreshRecording(item, showToast = true, showSpinner = true) {
    if (!this._hass || !item?.id) return item?.recording || null;
    if (showSpinner) {
      this._resolvingIds.add(item.id);
      this._render();
    }
    try {
      const recording = await this._hass.callWS({
        type: "reolink_feed/resolve_recording",
        item_id: item.id,
      });
      item.recording = recording;
      if (showToast) {
        if (recording.status === "linked") this._showToast("Recording linked");
        else if (recording.status === "not_found") this._showToast("Recording not found");
        else this._showToast("Recording still pending");
      }
      return recording;
    } catch (err) {
      this._showToast(`Resolve failed: ${err?.message || err}`);
      return null;
    } finally {
      if (showSpinner) {
        this._resolvingIds.delete(item.id);
        this._render();
      }
    }
  }

  async _openFromThumbnail(item) {
    if (!item?.id) return;
    this._openInfoDialog(item);
  }

  _openInfoDialog(item) {
    if (!item?.id) return;
    this._infoDialog = { open: true, itemId: item.id };
    this._render();
  }

  _closeInfoDialog() {
    this._infoDialog = { open: false, itemId: "" };
    this._render();
  }

  async _deleteItem(item) {
    if (!this._hass || !item?.id) return;
    try {
      await this._hass.callWS({
        type: "reolink_feed/delete_item",
        item_id: item.id,
      });
      this._items = this._items.filter((existing) => existing.id !== item.id);
      this._applyFilters();
      this._closeInfoDialog();
      this._showToast("Detection deleted");
    } catch (err) {
      this._showToast(`Delete failed: ${err?.message || err}`);
    }
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
    return new Date(ts).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
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

  _formatDateTime(ts) {
    if (!ts) return "";
    return new Date(ts).toLocaleString([], {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }

  _mediaFolderDisplayPath(item) {
    const dt = new Date(item?.start_ts || Date.now());
    const year = dt.getFullYear();
    const month = String(dt.getMonth() + 1).padStart(2, "0");
    const day = String(dt.getDate()).padStart(2, "0");
    const label = normalizeCardLabel(item?.label);
    const labelTitleByLabel = {
      person: "Person",
      pet: "Pet",
      vehicle: "Vehicle",
      motion: "Motion",
      visitor: "Visitor",
    };
    const labelTitle = labelTitleByLabel[label] || "Person";
    const camera = item?.camera_name || "Camera";
    return `/Reolink/${camera}/Low Resolution/${year}-${month}-${day}/${labelTitle}`;
  }

  _fileExtensionForLabel(label) {
    if (label === "motion") return "mp4";
    if (label === "vehicle") return "mp4";
    if (label === "visitor") return "mp4";
    if (label === "pet") return "mp4";
    return "mp4";
  }

  _isPlausibleFilename(name) {
    if (!name) return false;
    if (name.length < 3 || name.length > 255) return false;
    if (/[\\/:*?"<>|\u0000-\u001f]/.test(name)) return false;
    if (!/\.[a-z0-9]{2,5}$/i.test(name)) return false;
    // Keep names readable; reject high-entropy/binary-looking outputs.
    if (!/^[\w .\-()]+$/i.test(name)) return false;
    return true;
  }

  _recordingFilename(item) {
    const localUrl = item?.recording?.local_url;
    if (typeof localUrl === "string" && localUrl) {
      const fromLocal = localUrl.split("?")[0].split("/").pop() || "";
      if (this._isPlausibleFilename(fromLocal)) return fromLocal;
    }
    const dt = new Date(item?.start_ts || Date.now());
    const yyyy = dt.getFullYear();
    const mm = String(dt.getMonth() + 1).padStart(2, "0");
    const dd = String(dt.getDate()).padStart(2, "0");
    const hh = String(dt.getHours()).padStart(2, "0");
    const mi = String(dt.getMinutes()).padStart(2, "0");
    const ss = String(dt.getSeconds()).padStart(2, "0");
    const label = normalizeCardLabel(item?.label) || "detection";
    return `${yyyy}${mm}${dd}_${hh}${mi}${ss}_${label}.${this._fileExtensionForLabel(label)}`;
  }

  _mediaFileDisplayPath(item) {
    return `${this._mediaFolderDisplayPath(item)}/${this._recordingFilename(item)}`;
  }

  _recordingLinkHref(item) {
    const localUrl = item?.recording?.local_url;
    if (typeof localUrl === "string" && localUrl) {
      return localUrl;
    }
    return "#";
  }

  _labelIcon(label) {
    const icon = this._labelIconName(label);
    const labelText = this._labelText(label);
    return `
      <span class="label-icon" title="${labelText}" aria-label="${labelText}">
        <ha-icon icon="${icon}"></ha-icon>
      </span>
    `;
  }

  _render() {
    if (!this.shadowRoot || !this._config) {
      return;
    }

    const pagedItems = this._pagedItems();
    const totalPages = this._totalPages();
    const filterPills = this._availableLabels
      .map((label) => {
        const active = this._activeLabels.has(label);
        const icon = this._labelIconName(label);
        return `
            <button class="filter-pill${active ? " active" : ""}" data-filter-label="${label}" aria-pressed="${active ? "true" : "false"}">
              <ha-icon icon="${icon}"></ha-icon>
              <span>${this._labelText(label)}</span>
            </button>
        `;
      })
      .join("");
    const listHtml = pagedItems
      .map((item) => {
        const image = item.snapshot_url
          ? `<img src="${item.snapshot_url}" alt="${item.camera_name}" loading="lazy" />`
          : `<div class="placeholder">${this._t("no_snapshot")}</div>`;

        return `
          <li class="item" data-id="${item.id}">
            <button class="thumb" aria-label="${this._t("open_recording_preview")}">
              ${image}
              <span class="overlay top-left">
                ${this._labelIcon(item.label)}
              </span>
              <span class="overlay bottom-left">
                <span class="line2">${this._formatTime(item.start_ts)} (${this._formatDuration(item.duration_s)})</span>
              </span>
            </button>
          </li>
        `;
      })
      .join("");
    const paginationHtml =
      this._filteredItems.length > this._pageSize()
        ? `
      <div class="pagination">
        <button class="page-nav" data-page-nav="prev" ${this._page <= 1 ? "disabled" : ""}>${this._t("previous")}</button>
        <span class="page-info">${this._t("page")} ${this._page} / ${totalPages}</span>
        <button class="page-nav" data-page-nav="next" ${this._page >= totalPages ? "disabled" : ""}>${this._t("next")}</button>
      </div>
      `
        : "";

    const infoItem = this._infoDialog.open
      ? this._items.find((item) => item.id === this._infoDialog.itemId) || null
      : null;
    const infoFileHref = infoItem ? this._recordingLinkHref(infoItem) : "";
    const infoFileName = infoItem ? this._recordingFilename(infoItem) : "";
    const infoFileLinkHtml =
      infoItem && infoFileHref && infoFileHref !== "#"
        ? `<a href="${infoFileHref}" target="_blank" rel="noopener" title="${this._mediaFileDisplayPath(infoItem)}">${infoFileName}</a>`
        : `<span title="${infoItem ? this._mediaFileDisplayPath(infoItem) : ""}">${infoFileName}</span>`;
    const infoMediaHtml =
      infoItem && infoItem.recording?.local_url
        ? `<video class="info-video" controls playsinline preload="metadata" src="${infoItem.recording.local_url}"></video>`
        : infoItem && infoItem.snapshot_url
          ? `<img class="info-snapshot" src="${infoItem.snapshot_url}" alt="${infoItem.camera_name || this._t("snapshot")}" loading="lazy" />`
          : `<div class="placeholder">${this._t("no_snapshot")}</div>`;
    const infoDialogHtml =
      this._infoDialog.open && infoItem
        ? `
      <ha-dialog open scrimClickAction="close" escapeKeyAction="close">
        <div class="info-head">
          <span>${this._t("detection_info")}</span>
          <button class="close-info-top" type="button" aria-label="${this._t("close_info_dialog")}">✕</button>
        </div>
        <div class="info-body">
          ${infoMediaHtml}
          <div><strong>${this._t("camera")}:</strong> ${infoItem.camera_name || "-"}</div>
          <div><strong>${this._t("timestamp")}:</strong> ${this._formatDateTime(infoItem.start_ts) || "-"}</div>
          <div><strong>${this._t("duration")}:</strong> ${this._formatDuration(infoItem.duration_s)}</div>
          <div><strong>${this._t("detection")}:</strong> ${this._labelText(infoItem.label)}</div>
          <div class="info-links">
            <a href="/history?entity_id=${encodeURIComponent(infoItem.source_entity_id || "")}" target="_blank" rel="noopener">${this._t("history")}</a>
            <a href="/logbook?entity_id=${encodeURIComponent(infoItem.source_entity_id || "")}" target="_blank" rel="noopener">${this._t("logbook")}</a>
          </div>
          <div>
            <span><strong>${this._t("file")}:</strong> </span>
            ${infoFileLinkHtml}
          </div>
        </div>
        <div class="info-actions">
          <button class="reset-info${this._resolvingIds.has(infoItem.id) ? " resolving" : ""}" type="button">
            <ha-icon icon="mdi:arrow-u-left-top"></ha-icon>
            <span>${this._t("reset")}</span>
          </button>
          <button class="delete-info" type="button">
            <ha-icon icon="mdi:trash-can-outline"></ha-icon>
            <span>${this._t("delete")}</span>
          </button>
        </div>
      </ha-dialog>
      `
        : "";

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 10px; }
        .topbar { display: flex; justify-content: flex-start; align-items: center; gap: 10px; margin-bottom: 10px; }
        .filters { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .filter-pill { border: 1px solid rgba(255,255,255,0.22); background: transparent; color: var(--primary-text-color); border-radius: 999px; height: 30px; padding: 0 10px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; font-size: 12px; opacity: 0.55; }
        .filter-pill ha-icon { --mdc-icon-size: 14px; }
        .filter-pill.active { border-color: #fff; color: #fff; opacity: 1; }
        .filter-pill:hover { opacity: 0.9; background: rgba(255,255,255,0.08); }
        .pagination { display: flex; justify-content: center; align-items: center; gap: 10px; margin-top: 10px; }
        .page-info { color: var(--secondary-text-color); font-size: 12px; min-width: 84px; text-align: center; }
        button.page-nav { border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); border-radius: 8px; height: 28px; padding: 0 10px; cursor: pointer; font-size: 12px; }
        button.page-nav:hover { background: var(--secondary-background-color); }
        button.page-nav:disabled { opacity: 0.6; cursor: default; }
        ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }
        .item { position: relative; padding: 0; border-radius: 10px; overflow: hidden; background: rgba(255, 255, 255, 0.04); }
        .thumb { position: relative; display: block; width: 100%; height: clamp(140px, 22vw, 190px); overflow: hidden; border-radius: 10px; background: #111; border: 1px solid var(--divider-color); padding: 0; cursor: pointer; appearance: none; -webkit-appearance: none; }
        .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
        .thumb::before { content: ""; position: absolute; inset: 0; box-shadow: inset 0 0 0 1px rgba(255,255,255,0.04), inset 0 -48px 40px rgba(0,0,0,0.45), inset 0 40px 28px rgba(0,0,0,0.30); pointer-events: none; z-index: 1; }
        .overlay { position: absolute; z-index: 3; display: inline-flex; align-items: center; }
        .overlay.top-left { top: 8px; left: 8px; }
        .overlay.bottom-left { left: 8px; bottom: 8px; max-width: calc(100% - 16px); }
        .placeholder { color: #ddd; font-size: 11px; padding: 8px; }
        .label-icon { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border-radius: 6px; background: rgba(0, 0, 0, 0.35); backdrop-filter: blur(2px); }
        .label-icon ha-icon { --mdc-icon-size: 18px; color: #fff; }
        .line2 { color: #fff; font-size: 12px; padding: 3px 7px; border-radius: 7px; background: rgba(0, 0, 0, 0.40); backdrop-filter: blur(2px); display: inline-block; max-width: 100%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; box-sizing: border-box; }
        .empty { color: var(--secondary-text-color); font-size: 13px; padding: 8px 2px; }
        .error { color: var(--error-color); font-size: 12px; white-space: pre-wrap; }
        ha-dialog { --dialog-content-padding: 0; }
        .info-head { padding: 14px 16px; font-size: 16px; font-weight: 600; border-bottom: 1px solid var(--divider-color); display: flex; justify-content: space-between; align-items: center; gap: 10px; }
        .info-body { padding: 12px 16px; display: grid; gap: 10px; color: var(--primary-text-color); }
        .info-links { display: flex; gap: 12px; }
        .info-links a, .info-body a { color: var(--primary-color); text-decoration: none; }
        .info-links a:hover, .info-body a:hover { text-decoration: underline; }
        .info-video { width: 100%; max-height: 280px; border-radius: 8px; border: 1px solid var(--divider-color); background: #000; }
        .info-snapshot { width: 100%; max-height: 280px; object-fit: cover; border-radius: 8px; border: 1px solid var(--divider-color); }
        .info-body .placeholder { width: 100%; height: 220px; border-radius: 8px; border: 1px solid var(--divider-color); display: grid; place-items: center; color: var(--secondary-text-color); background: rgba(255,255,255,0.03); font-size: 13px; }
        .info-actions { padding: 0 16px 14px 16px; display: flex; justify-content: space-between; gap: 10px; }
        .close-info-top { border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); border-radius: 8px; width: 34px; height: 34px; cursor: pointer; font-size: 20px; line-height: 1; display: inline-flex; align-items: center; justify-content: center; padding: 0; }
        .close-info-top:hover { background: var(--secondary-background-color); }
        .reset-info, .delete-info { border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); border-radius: 8px; height: 34px; padding: 0 12px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; }
        .reset-info:hover { background: var(--secondary-background-color); }
        .reset-info.resolving { opacity: 0.7; }
        .delete-info { border-color: #c03b3b; color: #d64545; }
        .delete-info:hover { background: rgba(214, 69, 69, 0.1); }
        .reset-info ha-icon, .delete-info ha-icon { --mdc-icon-size: 16px; }
      </style>
      <ha-card>
        <div class="topbar">
          <div class="filters">
            ${filterPills}
          </div>
        </div>
        ${this._error ? `<div class="error">${this._error}</div>` : ""}
        ${this._filteredItems.length ? `<ul>${listHtml}</ul>${paginationHtml}` : `<div class="empty">${this._t("no_detections")}</div>`}
      </ha-card>
      ${infoDialogHtml}
    `;

    this.shadowRoot.querySelectorAll("li.item").forEach((el) => {
      const id = el.getAttribute("data-id");
      const item = this._items.find((x) => x.id === id);
      if (!item) return;

      const thumb = el.querySelector("button.thumb");

      if (thumb) {
        thumb.addEventListener("click", (ev) => {
          ev.preventDefault();
          this._openFromThumbnail(item);
        });
      }
    });

    this.shadowRoot.querySelectorAll(".filter-pill").forEach((el) => {
      el.addEventListener("click", (ev) => {
        ev.preventDefault();
        const label = normalizeCardLabel(el.getAttribute("data-filter-label"));
        if (!SUPPORTED_CARD_LABELS.includes(label)) return;
        this._toggleLabelFilter(label);
      });
    });

    this.shadowRoot.querySelectorAll("button.page-nav").forEach((el) => {
      el.addEventListener("click", (ev) => {
        ev.preventDefault();
        const action = el.getAttribute("data-page-nav");
        if (action === "prev" && this._page > 1) {
          this._page -= 1;
          this._render();
          return;
        }
        if (action === "next" && this._page < this._totalPages()) {
          this._page += 1;
          this._render();
        }
      });
    });

    const closeInfoTopButton = this.shadowRoot.querySelector("button.close-info-top");
    closeInfoTopButton?.addEventListener("click", (ev) => {
      ev.preventDefault();
      this._closeInfoDialog();
    });
    const resetInfoButton = this.shadowRoot.querySelector("button.reset-info");
    resetInfoButton?.addEventListener("click", (ev) => {
      ev.preventDefault();
      if (!infoItem) return;
      this._refreshRecording(infoItem, true);
    });
    const deleteInfoButton = this.shadowRoot.querySelector("button.delete-info");
    deleteInfoButton?.addEventListener("click", (ev) => {
      ev.preventDefault();
      if (!infoItem) return;
      this._deleteItem(infoItem);
    });
    const infoDialogEl = this.shadowRoot.querySelector("ha-dialog");
    infoDialogEl?.addEventListener("closed", () => {
      if (this._infoDialog.open) {
        this._closeInfoDialog();
      }
    });
    infoDialogEl?.addEventListener("close", () => {
      if (this._infoDialog.open) {
        this._closeInfoDialog();
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
  description: "Timeline of Reolink detections",
});

class ReolinkFeedCardEditor extends HTMLElement {
  constructor() {
    super();
    this._config = {};
    this._hass = null;
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _languageCode() {
    const raw = String(this._hass?.language || document.documentElement.lang || "en").toLowerCase();
    if (CARD_I18N[raw]) return raw;
    const base = raw.split("-")[0];
    if (CARD_I18N[base]) return base;
    return "en";
  }

  _t(key) {
    const lang = this._languageCode();
    return CARD_I18N[lang]?.[key] || CARD_I18N.en[key] || key;
  }

  _emitConfig(next) {
    this._config = next;
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: next },
        bubbles: true,
        composed: true,
      })
    );
  }

  _onNumberChange(key, value, fallback) {
    const parsed = Number.parseInt(value, 10);
    const next = { ...this._config, [key]: Number.isFinite(parsed) ? parsed : fallback };
    this._emitConfig(next);
  }

  _render() {
    if (!this.shadowRoot) return;
    const pageSize = Number(this._config?.page_size ?? 20);
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        .grid { display: grid; gap: 10px; }
        .field { display: grid; gap: 4px; }
        label { color: var(--primary-text-color); font-size: 13px; }
        input[type="text"], input[type="number"] {
          border: 1px solid var(--divider-color);
          background: var(--card-background-color);
          color: var(--primary-text-color);
          border-radius: 8px;
          padding: 8px;
          font-size: 13px;
        }
      </style>
      <div class="grid">
        <div class="field">
          <label for="page_size">${this._t("page_size")}</label>
          <input id="page_size" type="number" min="1" max="100" value="${pageSize}" />
        </div>
      </div>
    `;

    this.shadowRoot.querySelector("#page_size")?.addEventListener("change", (ev) => {
      this._onNumberChange("page_size", ev.target.value, 20);
    });
  }
}

if (!customElements.get("reolink-feed-card-editor")) {
  customElements.define("reolink-feed-card-editor", ReolinkFeedCardEditor);
}
