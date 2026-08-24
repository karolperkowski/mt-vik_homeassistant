/**
 * mtviki-matrix-card
 *
 * A crosspoint grid for the MT-VIKI HDMI Matrix Home Assistant integration
 * (custom_components/mtviki_matrix). Columns are inputs, rows are outputs;
 * clicking a cell routes that input to that output via select.select_option.
 *
 * Single file, no build step, no external imports (Home Assistant's frontend
 * CSP and offline installs both rule out CDN dependencies). Served by the
 * integration itself at /mtviki_matrix/mtviki-matrix-card.js and registered
 * as a frontend resource automatically -- see custom_components/mtviki_matrix
 * /__init__.py. Nothing here imports from Home Assistant; it only relies on
 * the `hass` object's public runtime shape (hass.states, hass.entities,
 * hass.callService), the same contract every custom Lovelace card uses.
 *
 * Card config:
 *   type: custom:mtviki-matrix-card
 *   title: "HDMI Matrix"              # optional
 *   device: <device_id>                # optional, scopes auto-discovery to
 *                                       # one mtviki_matrix device when more
 *                                       # than one is configured
 *   outputs:                           # optional; explicit entity_id list.
 *     - select.hdmi_matrix_output_1    # When omitted, output selects are
 *     - select.hdmi_matrix_output_2    # auto-discovered (see below).
 *   scenes:                            # optional; rendered as a button row.
 *     - button.hdmi_matrix_scene_1
 *     - button.hdmi_matrix_scene_2
 */

const DOMAIN = "mtviki_matrix";
const CARD_TAG = "mtviki-matrix-card";

// The two "config" selects the integration also creates (per-output HDCP,
// per-input EDID) are select entities too, so auto-discovery has to be able
// to tell them apart from the routing selects without any HA-side help.
// This is the exact literal option list select.py's MTVikiOutputHdcpSelect
// uses (see const.py HDCP_MODES); it's a raw value, never translated, so it
// is safe to compare against verbatim.
const HDCP_OPTION_SET = JSON.stringify(["off", "hdcp_1_4", "hdcp_2_0", "hdcp_2_2"]);

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/** Trailing integer in a string ("HDMI Matrix Output 12" -> 12), else null. */
function trailingNumber(text) {
  if (!text) return null;
  const match = String(text).match(/(\d+)\s*$/);
  return match ? Number.parseInt(match[1], 10) : null;
}

class MtvikiMatrixCard extends HTMLElement {
  static getStubConfig() {
    return { type: `custom:${CARD_TAG}`, title: "MT-VIKI Matrix" };
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
    this._hass = null;
    this._outputEntityIds = null; // resolved (auto-discovered or explicit)
    this._built = false;
    // Delegated listener survives innerHTML swaps on re-render, so it is
    // bound once here rather than re-attached after every _render().
    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
  }

  setConfig(config) {
    if (!config || typeof config !== "object") {
      throw new Error("mtviki-matrix-card: invalid configuration");
    }
    if (config.outputs !== undefined) {
      if (!Array.isArray(config.outputs) || !config.outputs.every((v) => typeof v === "string")) {
        throw new Error("mtviki-matrix-card: 'outputs' must be a list of entity ids");
      }
    }
    if (config.scenes !== undefined) {
      if (!Array.isArray(config.scenes) || !config.scenes.every((v) => typeof v === "string")) {
        throw new Error("mtviki-matrix-card: 'scenes' must be a list of entity ids");
      }
    }
    this._config = config;
    this._outputEntityIds = config.outputs && config.outputs.length ? config.outputs.slice() : null;
    this._built = false;
    if (this._hass) {
      this._render();
    }
  }

  set hass(hass) {
    const previousHass = this._hass;
    this._hass = hass;
    if (!this._config) return;

    if (!this._outputEntityIds) {
      this._outputEntityIds = this._discoverOutputs(hass);
    }

    if (!this._shouldRender(previousHass, hass)) return;
    this._render();
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    const rows = (this._outputEntityIds && this._outputEntityIds.length) || 3;
    const hasScenes = !!(this._config && Array.isArray(this._config.scenes) && this._config.scenes.length);
    return 1 + rows + (hasScenes ? 1 : 0);
  }

  // ------------------------------------------------------------ discovery

  /**
   * Auto-discover the matrix's output routing selects.
   *
   * Preferred path: hass.entities, the entity-registry snapshot the modern
   * frontend hands to every card. Each entry carries `platform` (the
   * integration domain that created it -- authoritative and unaffected by
   * renames) and `entity_category`; the routing selects are the only
   * mtviki_matrix `select.*` entities with no entity_category (the HDCP and
   * EDID selects are both entity_category "config"). This is far more
   * robust than pattern-matching entity_id, which is a user-renameable slug
   * with no stable relationship to the integration's internal unique_id.
   *
   * Fallback (older frontends that don't expose hass.entities to cards, or
   * a card rendered outside a full dashboard): group all select.* entities
   * by their exact `options` array. The routing selects are the only group
   * whose options are neither all-numeric (the EDID presets, "1".."16")
   * nor the fixed HDCP mode list; the largest remaining group wins.
   */
  _discoverOutputs(hass) {
    const deviceFilter = this._config && this._config.device;
    let candidates = [];

    const registry = hass.entities;
    if (registry) {
      candidates = Object.keys(registry).filter((entityId) => {
        if (!entityId.startsWith("select.")) return false;
        const entry = registry[entityId];
        if (!entry || entry.platform !== DOMAIN) return false;
        if (entry.entity_category) return false;
        if (deviceFilter && entry.device_id !== deviceFilter) return false;
        return true;
      });
    }

    if (!candidates.length) {
      const groups = new Map();
      for (const entityId of Object.keys(hass.states)) {
        if (!entityId.startsWith("select.")) continue;
        const state = hass.states[entityId];
        const options = state && state.attributes && state.attributes.options;
        if (!Array.isArray(options) || options.length < 2) continue;
        if (options.every((option) => /^\d+$/.test(option))) continue; // EDID
        const key = JSON.stringify(options);
        if (key === HDCP_OPTION_SET) continue;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(entityId);
      }
      let best = [];
      for (const group of groups.values()) {
        if (group.length > best.length) best = group;
      }
      candidates = best;
    }

    candidates.sort((a, b) => {
      const na = trailingNumber(hass.states[a] && hass.states[a].attributes && hass.states[a].attributes.friendly_name);
      const nb = trailingNumber(hass.states[b] && hass.states[b].attributes && hass.states[b].attributes.friendly_name);
      if (na !== null && nb !== null && na !== nb) return na - nb;
      return a.localeCompare(b);
    });

    return candidates;
  }

  // ------------------------------------------------------------- updating

  _watchedEntityIds() {
    const ids = [];
    if (this._outputEntityIds) ids.push(...this._outputEntityIds);
    if (this._config && Array.isArray(this._config.scenes)) ids.push(...this._config.scenes);
    return ids;
  }

  _shouldRender(previousHass, hass) {
    if (!this._built || !previousHass) return true;
    for (const entityId of this._watchedEntityIds()) {
      if (previousHass.states[entityId] !== hass.states[entityId]) return true;
    }
    return false;
  }

  // -------------------------------------------------------------- events

  _onClick(ev) {
    if (!this._hass) return;
    const cell = ev.target.closest(".cell");
    if (cell) {
      if (cell.disabled) return;
      this._hass.callService("select", "select_option", {
        entity_id: cell.dataset.entity,
        option: cell.dataset.option,
      });
      return;
    }
    const sceneButton = ev.target.closest(".scene-button");
    if (sceneButton && !sceneButton.disabled) {
      this._hass.callService("button", "press", { entity_id: sceneButton.dataset.scene });
    }
  }

  // -------------------------------------------------------------- render

  _render() {
    this._built = true;
    const hass = this._hass;
    const config = this._config || {};
    const outputIds = this._outputEntityIds || [];

    if (!outputIds.length) {
      this.shadowRoot.innerHTML = `
        ${this._styles()}
        <ha-card>
          <div class="card-content warning">
            No MT-VIKI matrix output selects were found. Set <code>outputs</code>
            in the card configuration, or confirm the integration is loaded.
          </div>
        </ha-card>`;
      return;
    }

    const firstState = hass.states[outputIds[0]];
    const inputs = (firstState && firstState.attributes && firstState.attributes.options) || [];

    const rowsHtml = outputIds.map((entityId) => this._rowHtml(hass, entityId, inputs)).join("");
    const scenesHtml = this._scenesHtml(hass, config.scenes);
    const titleHtml = config.title
      ? `<h1 class="card-header">${escapeHtml(config.title)}</h1>`
      : "";

    this.shadowRoot.innerHTML = `
      ${this._styles()}
      <ha-card>
        ${titleHtml}
        <div class="card-content">
          <div class="grid-scroll">
            <table class="crosspoint">
              <thead>
                <tr>
                  <th class="corner"></th>
                  ${inputs.map((label) => `<th class="col-label">${escapeHtml(label)}</th>`).join("")}
                </tr>
              </thead>
              <tbody>
                ${rowsHtml}
              </tbody>
            </table>
          </div>
          ${scenesHtml}
        </div>
      </ha-card>`;
  }

  _rowHtml(hass, entityId, inputs) {
    const state = hass.states[entityId];
    const unavailable = !state || state.state === "unavailable";
    const label = (state && state.attributes && state.attributes.friendly_name) || entityId;
    const current = state ? state.state : null;

    const cells = inputs
      .map((inputLabel) => {
        const active = !unavailable && current === inputLabel;
        return `<td>
          <button
            type="button"
            class="cell${active ? " active" : ""}"
            data-entity="${escapeHtml(entityId)}"
            data-option="${escapeHtml(inputLabel)}"
            aria-pressed="${active}"
            aria-label="${escapeHtml(label)}: ${escapeHtml(inputLabel)}"
            ${unavailable ? "disabled" : ""}
          ></button>
        </td>`;
      })
      .join("");

    return `<tr class="${unavailable ? "unavailable" : ""}">
      <th class="row-label" scope="row">${escapeHtml(label)}</th>
      ${cells}
    </tr>`;
  }

  _scenesHtml(hass, scenes) {
    if (!Array.isArray(scenes) || !scenes.length) return "";
    const buttons = scenes
      .map((entityId) => {
        const state = hass.states[entityId];
        const unavailable = !state || state.state === "unavailable";
        const label = (state && state.attributes && state.attributes.friendly_name) || entityId;
        return `<button
          type="button"
          class="scene-button"
          data-scene="${escapeHtml(entityId)}"
          ${unavailable ? "disabled" : ""}
        >${escapeHtml(label)}</button>`;
      })
      .join("");
    return `<div class="scenes">${buttons}</div>`;
  }

  _styles() {
    return `
      <style>
        :host { display: block; }
        ha-card { padding: 0; }
        .card-header {
          padding: 16px 16px 0;
          font-size: 1.2rem;
          color: var(--ha-card-header-color, var(--primary-text-color));
          font-family: var(--ha-card-header-font-family, inherit);
        }
        .card-content { padding: 16px; }
        .warning { color: var(--error-color, #db4437); }
        .grid-scroll { overflow-x: auto; }
        table.crosspoint { border-collapse: collapse; width: 100%; }
        table.crosspoint th, table.crosspoint td { padding: 4px; text-align: center; }
        th.corner { min-width: 88px; }
        th.col-label {
          font-weight: 500;
          font-size: 0.82em;
          color: var(--secondary-text-color);
          white-space: nowrap;
          padding: 4px 6px;
        }
        th.row-label {
          font-weight: 500;
          text-align: right;
          padding-right: 10px;
          white-space: nowrap;
          color: var(--primary-text-color);
        }
        tr.unavailable th.row-label,
        tr.unavailable td {
          opacity: 0.4;
        }
        .cell {
          width: 30px;
          height: 30px;
          border-radius: 50%;
          border: 2px solid var(--divider-color, #e0e0e0);
          background: var(--secondary-background-color, transparent);
          cursor: pointer;
          padding: 0;
          transition: background-color 0.15s ease, border-color 0.15s ease;
        }
        .cell:hover:not(:disabled) { border-color: var(--primary-color); }
        .cell:disabled { cursor: not-allowed; }
        .cell.active {
          background: var(--primary-color);
          border-color: var(--primary-color);
        }
        .scenes {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin-top: 16px;
          padding-top: 16px;
          border-top: 1px solid var(--divider-color, #e0e0e0);
        }
        .scene-button {
          padding: 6px 14px;
          border-radius: 16px;
          border: none;
          background: var(--primary-color);
          color: var(--text-primary-color, #fff);
          font-size: 0.85em;
          cursor: pointer;
        }
        .scene-button:disabled {
          background: var(--disabled-color, #bdbdbd);
          cursor: not-allowed;
          opacity: 0.6;
        }
      </style>`;
  }
}

customElements.define(CARD_TAG, MtvikiMatrixCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: CARD_TAG,
  name: "MT-VIKI Matrix Crosspoint",
  description: "Crosspoint grid for a MT-VIKI HDMI matrix: click a cell to route an input to an output.",
  preview: false,
});
