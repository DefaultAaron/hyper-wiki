/*
 * Hyper-Wiki HW-006 test driver. TEST ONLY: install it only in the throwaway profile that
 * spike.py creates. It does two things in that profile:
 *
 * 1. Observes: logs every Zotero.Notifier event, with a millisecond timestamp, to the JSONL
 *    file named by the pref extensions.hw006.logPath. This is the "plugin notifications"
 *    candidate for change detection.
 * 2. Drives: serves POST /hw006/act on Zotero's local server, so the harness can apply
 *    scripted changes (create, edit, trash, restore, erase, attach, annotate...) through
 *    Zotero's own JavaScript API, as a user would through the interface.
 *
 * Guard: it does nothing unless the open data directory contains the marker file
 * hw006-throwaway, which only spike.py creates. Hyper-Wiki itself never writes to Zotero.
 */

var HW006 = null;

const MARKER = "hw006-throwaway";
const ENDPOINTS = ["/hw006/ping", "/hw006/act", "/hw006/quit"];

function install() {}
function uninstall() {}

async function startup() {
  await Zotero.initializationPromise;
  let marker = PathUtils.join(Zotero.DataDirectory.dir, MARKER);
  if (!(await IOUtils.exists(marker))) {
    Zotero.debug(`HW-006 driver: no ${MARKER} in the data directory; staying inactive`);
    return;
  }
  HW006 = new Driver(Zotero.Prefs.get("extensions.hw006.logPath", true));
  HW006.start();
}

function shutdown() {
  if (HW006) {
    HW006.stop();
    HW006 = null;
  }
}

function libraryID() {
  return Zotero.Libraries.userLibraryID;
}

function itemByKey(key) {
  let item = Zotero.Items.getByLibraryAndKey(libraryID(), key);
  if (!item) throw new Error(`no item ${key}`);
  return item;
}

function collectionByKey(key) {
  let collection = Zotero.Collections.getByLibraryAndKey(libraryID(), key);
  if (!collection) throw new Error(`no collection ${key}`);
  return collection;
}

function newItem(args) {
  let item = new Zotero.Item(args.itemType || "journalArticle");
  item.libraryID = libraryID();
  for (let [field, value] of Object.entries(args.fields || {})) {
    item.setField(field, value);
  }
  if (args.creators) {
    item.setCreators(args.creators.map((c) => ({ ...c, creatorType: c.creatorType || "author" })));
  }
  for (let tag of args.tags || []) {
    item.addTag(tag);
  }
  if (args.collectionKey) {
    item.addToCollection(collectionByKey(args.collectionKey).id);
  }
  return item;
}

// Each action returns the keys it touched, so the harness can match observations to it.
const ACTIONS = {
  async create_item(args) {
    let item = newItem(args);
    await item.saveTx();
    return { keys: [item.key] };
  },

  async create_items_bulk(args) {
    let keys = [];
    await Zotero.DB.executeTransaction(async () => {
      for (let i = 0; i < args.count; i++) {
        let item = newItem({ fields: { title: `${args.titlePrefix} ${i + 1}` } });
        await item.save();
        keys.push(item.key);
      }
    });
    return { keys };
  },

  async import_file(args) {
    let item = await Zotero.Attachments.importFromFile({
      file: args.path,
      parentItemID: itemByKey(args.parentKey).id,
    });
    return { keys: [item.key], attachmentPath: item.attachmentPath };
  },

  async link_file(args) {
    let item = await Zotero.Attachments.linkFromFile({
      file: args.path,
      parentItemID: itemByKey(args.parentKey).id,
    });
    return { keys: [item.key], attachmentPath: item.attachmentPath };
  },

  async file_info(args) {
    let item = itemByKey(args.key);
    return {
      keys: [],
      attachmentPath: item.attachmentPath,
      filePath: item.getFilePath() || null,
      baseAttachmentPath: Zotero.Prefs.get("baseAttachmentPath") || null,
    };
  },

  async edit_field(args) {
    let item = itemByKey(args.key);
    item.setField(args.field, args.value);
    await item.saveTx();
    return { keys: [item.key] };
  },

  async add_note(args) {
    let note = new Zotero.Item("note");
    note.libraryID = libraryID();
    note.parentID = itemByKey(args.parentKey).id;
    note.setNote(args.html);
    await note.saveTx();
    return { keys: [note.key] };
  },

  async edit_note(args) {
    let note = itemByKey(args.key);
    note.setNote(args.html);
    await note.saveTx();
    return { keys: [note.key] };
  },

  async add_annotation(args) {
    let attachment = itemByKey(args.attachmentKey);
    let key = Zotero.DataObjectUtilities.generateKey();
    await Zotero.Annotations.saveFromJSON(attachment, {
      key,
      type: "highlight",
      text: args.text,
      comment: args.comment || "",
      color: "#ffd400",
      pageLabel: "1",
      sortIndex: "00000|000000|00000",
      position: { pageIndex: 0, rects: [[72, 700, 300, 712]] },
    });
    return { keys: [key] };
  },

  async create_collection(args) {
    let collection = new Zotero.Collection();
    collection.libraryID = libraryID();
    collection.name = args.name;
    if (args.parentKey) collection.parentKey = args.parentKey;
    await collection.saveTx();
    return { keys: [collection.key] };
  },

  async add_to_collection(args) {
    let item = itemByKey(args.key);
    item.addToCollection(collectionByKey(args.collectionKey).id);
    await item.saveTx();
    return { keys: [item.key, args.collectionKey] };
  },

  async add_tag(args) {
    let item = itemByKey(args.key);
    item.addTag(args.tag);
    await item.saveTx();
    return { keys: [item.key] };
  },

  async trash(args) {
    await Zotero.Items.trashTx(args.keys.map((key) => itemByKey(key).id));
    return { keys: args.keys };
  },

  async restore(args) {
    for (let key of args.keys) {
      let item = itemByKey(key);
      item.deleted = false;
      await item.saveTx();
    }
    return { keys: args.keys };
  },

  async erase(args) {
    await Zotero.Items.erase(args.keys.map((key) => itemByKey(key).id));
    return { keys: args.keys };
  },

  async empty_trash() {
    let keys = (await Zotero.Items.getAll(libraryID(), false, true))
      .filter((item) => item.deleted)
      .map((item) => item.key);
    await Zotero.Items.emptyTrash(libraryID());
    return { keys };
  },

  async trash_collection(args) {
    let collection = collectionByKey(args.key);
    collection.deleted = true;
    await collection.saveTx();
    return { keys: [args.key] };
  },

  async erase_collection(args) {
    await collectionByKey(args.key).eraseTx();
    return { keys: [args.key] };
  },

  async rename_file(args) {
    let item = itemByKey(args.key);
    let result = await item.renameAttachmentFile(args.name, { overwrite: false, unique: false });
    if (result !== true) throw new Error(`rename failed: ${result}`);
    return { keys: [item.key], attachmentPath: item.attachmentPath };
  },

  async state() {
    let library = Zotero.Libraries.userLibrary;
    return { keys: [], clientVersion: library.clientVersion, version: library.version };
  },
};

function makeEndpoint(handler, methods) {
  let Endpoint = function () {};
  Endpoint.prototype = {
    supportedMethods: methods,
    supportedDataTypes: ["application/json"],
    permitBookmarklet: false,
    // Zotero's server picks the calling convention from init.length: exactly one parameter
    // means "takes a request object, returns [status, type, body]".
    init: function (request) {
      return handler(request);
    },
  };
  return Endpoint;
}

function json(status, body) {
  return [status, "application/json", JSON.stringify(body)];
}

class Driver {
  constructor(logPath) {
    this.logPath = logPath;
    this.writes = Promise.resolve();
    this.observerID = null;
  }

  log(record) {
    if (!this.logPath) return;
    let line = JSON.stringify(record) + "\n";
    this.writes = this.writes
      .then(() => IOUtils.writeUTF8(this.logPath, line, { mode: "appendOrCreate" }))
      .catch((e) => Zotero.logError(e));
  }

  notify(event, type, ids, extraData) {
    let t = Date.now();
    let keys = [];
    for (let id of ids) {
      let extra = extraData && extraData[id];
      if (extra && extra.key) {
        keys.push(extra.key);
        continue;
      }
      let objects = safeObjectsClass(type);
      let object = objects && typeof id === "number" ? objects.get(id) : null;
      keys.push(object ? object.key : String(id));
    }
    this.log({ t, event, type, ids, keys, extraData: safeExtra(extraData) });
  }

  start() {
    this.observerID = Zotero.Notifier.registerObserver(
      { notify: (...args) => this.notify(...args) },
      null,
      "hw006"
    );
    Zotero.Server.Endpoints["/hw006/ping"] = makeEndpoint(async () => {
      let library = Zotero.Libraries.userLibrary;
      return json(200, {
        dataDir: Zotero.DataDirectory.dir,
        zoteroVersion: Zotero.version,
        platform: Zotero.platform,
        clientVersion: library.clientVersion,
        version: library.version,
      });
    }, ["GET"]);
    Zotero.Server.Endpoints["/hw006/act"] = makeEndpoint(async (request) => {
      let { action, args } = request.data || {};
      let handler = ACTIONS[action];
      if (!handler) return json(400, { ok: false, error: `unknown action ${action}` });
      let start = Date.now();
      try {
        let result = await handler(args || {});
        return json(200, { ok: true, start, done: Date.now(), ...result });
      } catch (e) {
        Zotero.logError(e);
        return json(500, { ok: false, start, done: Date.now(), error: String(e) });
      }
    }, ["POST"]);
    Zotero.Server.Endpoints["/hw006/quit"] = makeEndpoint(async () => {
      setTimeout(() => {
        Services.startup.quit(Components.interfaces.nsIAppStartup.eAttemptQuit);
      }, 200);
      return json(200, { ok: true });
    }, ["POST"]);
    Zotero.debug("HW-006 driver: active");
  }

  stop() {
    if (this.observerID) Zotero.Notifier.unregisterObserver(this.observerID);
    for (let path of ENDPOINTS) delete Zotero.Server.Endpoints[path];
  }
}

function safeObjectsClass(type) {
  try {
    return Zotero.DataObjectUtilities.getObjectsClassForObjectType(type);
  } catch (e) {
    return null;
  }
}

function safeExtra(extraData) {
  try {
    return JSON.parse(JSON.stringify(extraData || {}));
  } catch (e) {
    return { unserializable: String(e) };
  }
}
