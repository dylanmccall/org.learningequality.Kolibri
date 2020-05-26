import json
import operator
import os
import re

from configparser import ConfigParser

from kolibri_gnome.globals import KOLIBRI_HOME

CONTENT_EXTENSIONS_DIR = "/app/share/kolibri-content"

CONTENT_EXTENSION_RE = r"^org.learningequality.Kolibri.Content.(?P<name>\w+)$"


class KolibriContentChannel(object):
    def __init__(self, channel_id, node_ids, exclude_node_ids):
        self.__channel_id = channel_id
        self.__node_ids = node_ids
        self.__exclude_node_ids = exclude_node_ids

    @classmethod
    def from_json(cls, json):
        return cls(
            json.get("channel_id"), json.get("node_ids"), json.get("exclude_node_ids")
        )

    @property
    def channel_id(self):
        return self.__channel_id

    @property
    def node_ids(self):
        return self.__node_ids

    @property
    def exclude_node_ids(self):
        return self.__exclude_node_ids


class ContentExtension(object):
    """
    Represents a content extension, with details about the flatpak ref and
    support for an index of content which may be either cached or located in the
    content extension itself. We assume any ContentExtension instances with
    matching ref and commit must be the same.
    """

    def __init__(self, ref, name, commit, content_json=None):
        self.__ref = ref
        self.__name = name
        self.__commit = commit
        self.__content_json = content_json

    @classmethod
    def from_ref(cls, ref, commit):
        match = re.match(CONTENT_EXTENSION_RE, ref)
        if match:
            name = match.group("name")
            return cls(ref, name, commit, content_json=None)
        else:
            return None

    @classmethod
    def from_json(cls, json):
        return cls(
            json.get("ref"),
            json.get("name"),
            json.get("commit"),
            content_json=json.get("content"),
        )

    def to_json(self):
        return {
            "ref": self.ref,
            "name": self.name,
            "commit": self.commit,
            "content": self.content_json,
        }

    def __eq__(self, other):
        return hash(self) == hash(other)

    def __hash__(self):
        return hash((self.__ref, self.__name, self.__commit))

    @property
    def ref(self):
        return self.__ref

    @property
    def name(self):
        return self.__name

    @property
    def commit(self):
        return self.__commit

    def is_valid(self):
        return all(
            [os.path.isdir(self.content_dir), os.path.isfile(self.__content_json_path)]
        )

    @property
    def content_json(self):
        if self.__content_json is not None:
            return self.__content_json

        try:
            with open(self.__content_json_path, "r") as file:
                self.__content_json = json.load(file)
        except (OSError, json.JSONDecodeError):
            self.__content_json = {}

        return self.__content_json

    @property
    def channels(self):
        channels_json = self.content_json.get("channels", [])
        return set(map(KolibriContentChannel.from_json, channels_json))

    @property
    def base_dir(self):
        return os.path.join(CONTENT_EXTENSIONS_DIR, self.name)

    @property
    def content_dir(self):
        return os.path.join(self.base_dir, "content")

    @property
    def __content_json_path(self):
        return os.path.join(self.content_dir, "content.json")


class ContentExtensionsList(object):
    """
    Keeps track of a list of content extensions, either cached from a file in
    $KOLIBIR_HOME, or generated from /.flatpak-info. Multiple lists can be
    compared to detect changes in the environment.
    """

    CONTENT_EXTENSIONS_STATE_PATH = os.path.join(
        KOLIBRI_HOME, "content-extensions.json"
    )

    def __init__(self, extensions=set()):
        self.__extensions = set(extensions)

    @classmethod
    def from_flatpak_info(cls):
        extensions = set()

        flatpak_info = ConfigParser()
        flatpak_info.read("/.flatpak-info")
        app_extensions = flatpak_info.get("Instance", "app-extensions", fallback="")
        for extension_str in app_extensions.split(";"):
            extension_ref, extension_commit = extension_str.split("=")
            content_extension = ContentExtension.from_ref(
                extension_ref, commit=extension_commit
            )
            if content_extension and content_extension.is_valid():
                extensions.add(content_extension)

        return cls(extensions)

    @classmethod
    def from_cache(cls):
        extensions = set()

        try:
            with open(cls.CONTENT_EXTENSIONS_STATE_PATH, "r") as file:
                extensions_json = json.load(file)
        except (OSError, json.JSONDecodeError):
            pass
        else:
            extensions = set(map(ContentExtension.from_json, extensions_json))

        return cls(extensions)

    def write_to_cache(self):
        with open(self.CONTENT_EXTENSIONS_STATE_PATH, "w") as file:
            extensions_json = list(map(ContentExtension.to_json, self.__extensions))
            json.dump(extensions_json, file)

    @property
    def content_fallback_dirs(self):
        return list(map(operator.attrgetter("content_dir"), self.__extensions))

    def diff(self, other):
        return ContentExtensionsDiff(self, other)

    @staticmethod
    def removed(old, new):
        return old.__extensions.difference(new.__extensions)

    @staticmethod
    def added(old, new):
        return new.__extensions.difference(old.__extensions)

    @property
    def __extensions_dict(self):
        return dict((extension.ref, extension) for extension in self.__extensions)

    @property
    def __refs(self):
        return set(map(operator.attrgetter("ref"), self.__extensions))

    def __get_extension(self, ref):
        return self.__extensions_dict.get(ref)

    def __filter_extensions(self, refs):
        return set(map(self.__get_extension, refs))


class ContentExtensionOp(object):
    pass


class ContentExtensionsDiff(object):
    def __init__(self, old_extensions_list, new_extensions_list):
        self.__old_extensions_list = old_extensions_list
        self.__new_extensions_list = new_extensions_list

    def iter_content_changes(self):
        removed = set()
        for extension in self.removed_extensions:
            for channel in extension.channels:
                for node_id in channel.node_ids:
                    removed.add((channel.channel_id, 'node_id', node_id))
                for exclude_node_id in channel.exclude_node_ids:
                    removed.add((channel.channel_id, 'exclude_node_id', exclude_node_id))

        added = set()
        for extension in self.added_extensions:
            for channel in extension.channels:
                for node_id in channel.node_ids:
                    added.add((channel.channel_id, 'node_id', node_id))
                for exclude_node_id in channel.exclude_node_ids:
                    added.add((channel.channel_id, 'exclude_node_id', exclude_node_id))

    def do_stuff(self):
        channel_changes = dict()

        for channel_id, op, node_id:
            channel_ops = channel_changes.setdefault(channel_id, [])
            channel_ops.append((op, node_id))

    @property
    def removed_extensions(self):
        return ContentExtensionsList.removed(
            self.__old_extensions_list, self.__new_extensions_list
        )

    @property
    def added_extensions(self):
        return ContentExtensionsList.added(
            self.__old_extensions_list, self.__new_extensions_list
        )

