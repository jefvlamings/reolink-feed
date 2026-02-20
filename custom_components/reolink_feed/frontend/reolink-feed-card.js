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
    recording: "Recording",
    photo: "Photo",
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
    not_found: "Not found",
    download_failed: "Download failed",
    pending: "Pending",
    event: "Event",
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
    recording: "Opname",
    photo: "Foto",
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
    not_found: "Niet gevonden",
    download_failed: "Download mislukt",
    pending: "In behandeling",
    event: "Event",
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
    this._ignoreDialogCloseEvents = 0;
    this._handleDialogKeyDown = (ev) => {
      if (!this._infoDialog.open) return;
      if (ev.key === "ArrowLeft") {
        ev.preventDefault();
        this._openPreviousInfoItem();
      } else if (ev.key === "ArrowRight") {
        ev.preventDefault();
        this._openNextInfoItem();
      }
    };
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

  async _refreshRecording(item, showToast = true, showSpinner = true, finalAttempt = false) {
    if (!this._hass || !item?.id) return item?.recording || null;
    const keepDialogOpenForItem =
      this._infoDialog.open && this._infoDialog.itemId === item.id;
    if (keepDialogOpenForItem && showSpinner) {
      this._ignoreDialogCloseEvents = 2;
    }
    if (showSpinner) {
      this._resolvingIds.add(item.id);
      this._render();
    }
    try {
      const recording = await this._hass.callWS({
        type: "reolink_feed/resolve_recording",
        item_id: item.id,
        final_attempt: finalAttempt,
      });
      item.recording = recording;
      if (showToast) {
        if (recording.status === "linked") this._showToast("Recording linked");
        else if (recording.status === "not_found") this._showToast("Recording not found");
        else if (recording.status === "download_failed") this._showToast("Recording download failed");
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
    if (this._infoDialog.open) {
      this._infoDialog.itemId = item.id;
      this._updateInfoDialogInPlace();
      return;
    }
    this._infoDialog = { open: true, itemId: item.id };
    window.addEventListener("keydown", this._handleDialogKeyDown);
    this._render();
  }

  _closeInfoDialog() {
    this._infoDialog = { open: false, itemId: "" };
    window.removeEventListener("keydown", this._handleDialogKeyDown);
    this._render();
  }

  _currentInfoItemIndex() {
    if (!this._infoDialog.open) return -1;
    return this._items.findIndex((item) => item.id === this._infoDialog.itemId);
  }

  _currentInfoItem() {
    if (!this._infoDialog.open) return null;
    return this._items.find((item) => item.id === this._infoDialog.itemId) || null;
  }

  _openPreviousInfoItem() {
    const idx = this._currentInfoItemIndex();
    if (idx <= 0) return;
    const prevItem = this._items[idx - 1];
    if (!prevItem) return;
    this._infoDialog.itemId = prevItem.id;
    this._updateInfoDialogInPlace();
  }

  _openNextInfoItem() {
    const idx = this._currentInfoItemIndex();
    if (idx < 0 || idx >= this._items.length - 1) return;
    const nextItem = this._items[idx + 1];
    if (!nextItem) return;
    this._infoDialog.itemId = nextItem.id;
    this._updateInfoDialogInPlace();
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

  _formatDate(ts) {
    if (!ts) return "";
    return new Date(ts).toLocaleDateString([], {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  }

  _formatWeekdayAndTime(ts) {
    if (!ts) return "";
    return new Date(ts).toLocaleString([], {
      weekday: "long",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }

  _formatStartEndLine(item) {
    if (!item?.start_ts) return "-";
    const start = new Date(item.start_ts);
    const end = item?.end_ts
      ? new Date(item.end_ts)
      : new Date(start.getTime() + Math.max(0, Number(item?.duration_s) || 0) * 1000);
    return `${this._formatDate(start.toISOString())} ${this._formatTime(start.toISOString())} - ${this._formatTime(end.toISOString())}`;
  }

  _infoHeaderTitle(item) {
    if (!item) return this._t("detection_info");
    return `${item.camera_name || "-"} - ${this._formatWeekdayAndTime(item.start_ts) || "-"} (${this._formatDuration(item.duration_s)})`;
  }

  _infoRecordingFallbackText(status) {
    if (status === "not_found") return this._t("not_found");
    if (status === "download_failed") return this._t("download_failed");
    return this._t("pending");
  }

  _buildInfoFileLinkHtml(item) {
    const infoFileHref = this._recordingLinkHref(item);
    const infoFileName = this._recordingFilename(item);
    const infoRecordingStatus = item?.recording?.status || "pending";
    if (infoFileHref && infoFileHref !== "#") {
      return `<a href="${infoFileHref}" target="_blank" rel="noopener" title="${this._mediaFileDisplayPath(item)}">${infoFileName}</a>`;
    }
    return `<span>${this._infoRecordingFallbackText(infoRecordingStatus)}</span>`;
  }

  _buildInfoPhotoLinkHtml(item) {
    const infoPhotoHref = this._snapshotLinkHref(item);
    const infoPhotoName = this._snapshotFilename(item);
    if (infoPhotoHref && infoPhotoHref !== "#") {
      return `<a href="${infoPhotoHref}" target="_blank" rel="noopener" title="${infoPhotoHref}">${infoPhotoName}</a>`;
    }
    return `<span title="${infoPhotoHref || ""}">${infoPhotoName || "-"}</span>`;
  }

  _buildInfoMediaHtml(item) {
    return `
      <div class="info-media-frame">
        ${
          item && item.recording?.local_url
            ? `<video class="info-video" controls autoplay muted playsinline preload="auto" src="${item.recording.local_url}"></video>`
            : item && item.snapshot_url
              ? `<img class="info-snapshot" src="${item.snapshot_url}" alt="${item.camera_name || this._t("snapshot")}" loading="lazy" />`
              : `<div class="placeholder">${this._t("no_snapshot")}</div>`
        }
      </div>
    `;
  }

  _setupInfoVideoAutoplay() {
    if (!this._infoDialog.open) return;
    const infoVideoEl = this.shadowRoot?.querySelector("video.info-video");
    if (!infoVideoEl) return;
    const currentItem = this._currentInfoItem();
    const requestedOffset = Number(currentItem?.recording?.start_offset_s);
    const initialOffsetSeconds =
      Number.isFinite(requestedOffset) && requestedOffset >= 0
        ? Math.max(0, requestedOffset - 2)
        : 0;
    let initialSeekApplied = false;
    const applyInitialSeek = () => {
      if (initialSeekApplied) return;
      const duration = Number(infoVideoEl.duration);
      if (!Number.isFinite(duration) || duration <= 0) return;
      if (initialOffsetSeconds <= 0) {
        initialSeekApplied = true;
        return;
      }
      const safeOffset = Math.min(initialOffsetSeconds, Math.max(0, duration - 0.25));
      try {
        infoVideoEl.currentTime = safeOffset;
        initialSeekApplied = true;
      } catch (_err) {
        // Some browsers may reject early seek before enough media is buffered.
      }
    };
    infoVideoEl.muted = true;
    infoVideoEl.playsInline = true;
    infoVideoEl.preload = "auto";
    if (infoVideoEl.readyState >= 1) {
      applyInitialSeek();
    } else {
      infoVideoEl.addEventListener("loadedmetadata", applyInitialSeek, { once: true });
    }
    infoVideoEl.load();
    const playPromise = infoVideoEl.play();
    if (playPromise && typeof playPromise.catch === "function") {
      playPromise.catch(() => {
        // Autoplay may be blocked by browser policy; keep controls for manual start.
      });
    }
  }

  _updateInfoDialogInPlace() {
    if (!this._infoDialog.open || !this.shadowRoot) return;
    const dialogEl = this.shadowRoot.querySelector("ha-dialog");
    const infoItem = this._currentInfoItem();
    if (!dialogEl || !infoItem) {
      this._render();
      return;
    }

    const titleIconEl = this.shadowRoot.querySelector(".info-title ha-icon");
    const titleTextEl = this.shadowRoot.querySelector(".info-title-text");
    const mediaSlotEl = this.shadowRoot.querySelector(".info-media-slot");
    const detectionValueEl = this.shadowRoot.querySelector(".info-detection-value");
    const startEndEl = this.shadowRoot.querySelector(".info-start-end");
    const recordingValueEl = this.shadowRoot.querySelector(".info-recording-value");
    const photoValueEl = this.shadowRoot.querySelector(".info-photo-value");
    const prevInfoButton = this.shadowRoot.querySelector("button.prev-info");
    const nextInfoButton = this.shadowRoot.querySelector("button.next-info");
    const resetInfoButton = this.shadowRoot.querySelector("button.reset-info");
    const resetIcon = resetInfoButton?.querySelector("ha-icon");

    titleIconEl?.setAttribute("icon", this._labelIconName(infoItem.label));
    if (titleTextEl) titleTextEl.textContent = this._infoHeaderTitle(infoItem);
    if (mediaSlotEl) mediaSlotEl.innerHTML = this._buildInfoMediaHtml(infoItem);
    if (detectionValueEl) detectionValueEl.textContent = this._labelText(infoItem.label);
    if (startEndEl) startEndEl.textContent = this._formatStartEndLine(infoItem);
    if (recordingValueEl) recordingValueEl.innerHTML = this._buildInfoFileLinkHtml(infoItem);
    if (photoValueEl) photoValueEl.innerHTML = this._buildInfoPhotoLinkHtml(infoItem);

    const idx = this._currentInfoItemIndex();
    if (prevInfoButton) prevInfoButton.disabled = idx <= 0;
    if (nextInfoButton) nextInfoButton.disabled = idx >= this._items.length - 1;
    if (resetInfoButton && resetIcon) {
      const isResolving = this._resolvingIds.has(infoItem.id);
      resetInfoButton.classList.toggle("resolving", isResolving);
      resetInfoButton.disabled = isResolving;
      resetIcon.setAttribute("icon", isResolving ? "mdi:loading" : "mdi:arrow-u-left-top");
      resetIcon.classList.toggle("spin", isResolving);
    }

    this._setupInfoVideoAutoplay();
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

  _snapshotFilename(item) {
    const snapshotUrl = item?.snapshot_url;
    if (typeof snapshotUrl === "string" && snapshotUrl) {
      const fromLocal = snapshotUrl.split("?")[0].split("/").pop() || "";
      if (this._isPlausibleFilename(fromLocal)) return fromLocal;
    }
    return "";
  }

  _snapshotLinkHref(item) {
    const snapshotUrl = item?.snapshot_url;
    if (typeof snapshotUrl === "string" && snapshotUrl) {
      return snapshotUrl;
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
    const infoLabelIcon = infoItem ? this._labelIconName(infoItem.label) : "mdi:account";
    const infoHeaderTitle = this._infoHeaderTitle(infoItem);
    const infoIsResolving = infoItem ? this._resolvingIds.has(infoItem.id) : false;
    const infoFileLinkHtml = infoItem ? this._buildInfoFileLinkHtml(infoItem) : "<span>-</span>";
    const infoPhotoLinkHtml = infoItem ? this._buildInfoPhotoLinkHtml(infoItem) : "<span>-</span>";
    const infoMediaHtml = infoItem ? this._buildInfoMediaHtml(infoItem) : "";
    const infoDialogHtml =
      this._infoDialog.open && infoItem
        ? `
      <ha-dialog open scrimClickAction="close" escapeKeyAction="close">
        <div class="info-head">
          <span class="info-title">
            <ha-icon icon="${infoLabelIcon}"></ha-icon>
            <span class="info-title-text">${infoHeaderTitle}</span>
          </span>
          <div class="info-head-actions">
            <button
              class="nav-info prev-info"
              type="button"
              aria-label="${this._t("previous")}"
              ${this._currentInfoItemIndex() <= 0 ? "disabled" : ""}
            ><ha-icon icon="mdi:chevron-left"></ha-icon></button>
            <button
              class="nav-info next-info"
              type="button"
              aria-label="${this._t("next")}"
              ${this._currentInfoItemIndex() >= this._items.length - 1 ? "disabled" : ""}
            ><ha-icon icon="mdi:chevron-right"></ha-icon></button>
            <button class="close-info-top" type="button" aria-label="${this._t("close_info_dialog")}">✕</button>
          </div>
        </div>
        <div class="info-body">
          <div class="info-media-slot">${infoMediaHtml}</div>
          <div><strong>${this._t("detection")}:</strong> <span class="info-detection-value">${this._labelText(infoItem.label)}</span></div>
          <div><strong>${this._t("event")}:</strong> <span class="info-start-end">${this._formatStartEndLine(infoItem)}</span></div>
          <div class="info-links">
            <a href="/history?entity_id=${encodeURIComponent(infoItem.source_entity_id || "")}" target="_blank" rel="noopener">${this._t("history")}</a>
            <a href="/logbook?entity_id=${encodeURIComponent(infoItem.source_entity_id || "")}" target="_blank" rel="noopener">${this._t("logbook")}</a>
          </div>
          <div>
            <span><strong>${this._t("recording")}:</strong> </span>
            <span class="info-recording-value">${infoFileLinkHtml}</span>
          </div>
          <div>
            <span><strong>${this._t("photo")}:</strong> </span>
            <span class="info-photo-value">${infoPhotoLinkHtml}</span>
          </div>
        </div>
          <button slot="secondaryAction" class="reset-info${infoIsResolving ? " resolving" : ""}" type="button" ${infoIsResolving ? "disabled" : ""}>
            <ha-icon class="${infoIsResolving ? "spin" : ""}" icon="${infoIsResolving ? "mdi:loading" : "mdi:arrow-u-left-top"}"></ha-icon>
            <span>${this._t("reset")}</span>
          </button>
          <button slot="primaryAction" class="delete-info" type="button">
            <ha-icon icon="mdi:trash-can-outline"></ha-icon>
            <span>${this._t("delete")}</span>
          </button>
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
        ha-dialog {
          --dialog-content-padding: 0;
          --mdc-dialog-min-width: min(760px, 94vw);
          --mdc-dialog-max-width: min(760px, 94vw);
        }
        .info-head { padding: 14px 16px; font-size: 16px; font-weight: 600; border-bottom: 1px solid var(--divider-color); display: flex; justify-content: space-between; align-items: center; gap: 10px; }
        .info-body {
          padding: 12px 16px;
          display: grid;
          gap: 10px;
          color: var(--primary-text-color);
          min-width: min(720px, 90vw);
          box-sizing: border-box;
          max-height: min(68vh, 560px);
          overflow-y: auto;
          overflow-x: hidden;
        }
        .info-links { display: flex; gap: 12px; }
        .info-links a, .info-body a { color: var(--primary-color); text-decoration: none; }
        .info-links a:hover, .info-body a:hover { text-decoration: underline; }
        .info-media-frame {
          width: 100%;
          height: clamp(260px, 40vh, 420px);
          border-radius: 8px;
          border: 1px solid var(--divider-color);
          background: #000;
          overflow: hidden;
        }
        .info-video, .info-snapshot, .info-media-frame .placeholder {
          width: 100%;
          height: 100%;
          display: block;
        }
        .info-video { background: #000; object-fit: contain; }
        .info-snapshot { object-fit: cover; }
        .info-body .placeholder {
          display: grid;
          place-items: center;
          color: var(--secondary-text-color);
          background: rgba(255,255,255,0.05);
          font-size: 13px;
          box-sizing: border-box;
          overflow-wrap: anywhere;
          text-align: center;
          padding: 8px;
        }
        .info-head-actions { display: inline-flex; align-items: center; gap: 8px; }
        .info-title {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          min-width: 0;
        }
        .info-title ha-icon { --mdc-icon-size: 20px; flex: 0 0 auto; }
        .info-title span {
          display: block;
          min-width: 0;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .close-info-top { border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); border-radius: 8px; width: 34px; height: 34px; cursor: pointer; font-size: 20px; line-height: 1; display: inline-flex; align-items: center; justify-content: center; padding: 0; }
        .close-info-top:hover { background: var(--secondary-background-color); }
        .nav-info { border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); border-radius: 8px; width: 34px; height: 34px; cursor: pointer; line-height: 1; display: inline-flex; align-items: center; justify-content: center; padding: 0; }
        .nav-info:hover { background: var(--secondary-background-color); }
        .nav-info:disabled { opacity: 0.45; cursor: default; }
        .nav-info ha-icon { --mdc-icon-size: 20px; }
        .reset-info, .delete-info { border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); border-radius: 8px; height: 34px; padding: 0 12px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; }
        .reset-info:hover { background: var(--secondary-background-color); }
        .reset-info.resolving { opacity: 0.7; }
        .reset-info:disabled { cursor: default; }
        .spin { animation: rf-spin 1s linear infinite; }
        @keyframes rf-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
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
    const prevInfoButton = this.shadowRoot.querySelector("button.prev-info");
    prevInfoButton?.addEventListener("click", (ev) => {
      ev.preventDefault();
      this._openPreviousInfoItem();
    });
    const nextInfoButton = this.shadowRoot.querySelector("button.next-info");
    nextInfoButton?.addEventListener("click", (ev) => {
      ev.preventDefault();
      this._openNextInfoItem();
    });
    const resetInfoButton = this.shadowRoot.querySelector("button.reset-info");
    resetInfoButton?.addEventListener("click", (ev) => {
      ev.preventDefault();
      const currentInfoItem = this._currentInfoItem();
      if (!currentInfoItem) return;
      this._refreshRecording(currentInfoItem, true, true, true);
    });
    const deleteInfoButton = this.shadowRoot.querySelector("button.delete-info");
    deleteInfoButton?.addEventListener("click", (ev) => {
      ev.preventDefault();
      const currentInfoItem = this._currentInfoItem();
      if (!currentInfoItem) return;
      this._deleteItem(currentInfoItem);
    });
    const infoDialogEl = this.shadowRoot.querySelector("ha-dialog");
    this._setupInfoVideoAutoplay();
    infoDialogEl?.addEventListener("closed", () => {
      if (this._ignoreDialogCloseEvents > 0) {
        this._ignoreDialogCloseEvents -= 1;
        return;
      }
      if (this._infoDialog.open) {
        this._closeInfoDialog();
      }
    });
    infoDialogEl?.addEventListener("close", () => {
      if (this._ignoreDialogCloseEvents > 0) {
        this._ignoreDialogCloseEvents -= 1;
        return;
      }
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
