# SPDX-License-Identifier: BSD-3-Clause
"""ALSA UCM2 (.conf) lexer, recursive-descent parser, condition evaluator, and loader."""

from __future__ import annotations
import os
import posixpath
import re
from typing import Any, Dict, List, Optional, Tuple

from .discovery import Transport, run_alsaucm_command
from .model import (
    MachineAudioSnapshot,
    UcmProfile,
    UcmSectionDevice,
    UcmSectionModifier,
    UcmSequenceCmd,
    UcmVerb,
)


# ---------------------------------------------------------------------------
# UCM2 Tokenizer & Block AST
# ---------------------------------------------------------------------------

def _tokenize_ucm(text: str) -> List[Tuple[str, str, int]]:
    """Tokenize ALSA UCM2 config text into (kind, value, line_no)."""
    tokens: List[Tuple[str, str, int]] = []
    i = 0
    n = len(text)
    line = 1
    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch in " \t\r":
            i += 1
            continue
        if ch == "#":
            j = text.find("\n", i)
            if j < 0:
                j = n
            i = j
            continue
        if ch in "{}[]":
            tokens.append((ch, ch, line))
            i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            i += 1
            buf: List[str] = []
            while i < n and text[i] != quote:
                if text[i] == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    if nxt == "n":
                        buf.append("\n")
                    elif nxt == "t":
                        buf.append("\t")
                    else:
                        buf.append(nxt)
                    i += 2
                    continue
                if text[i] == "\n":
                    line += 1
                buf.append(text[i])
                i += 1
            if i < n and text[i] == quote:
                i += 1
            tokens.append(("STR", "".join(buf), line))
            continue
        if ch == "=":
            i += 1
            continue
        j = i
        while j < n and text[j] not in " \t\r\n{}[]\"'#=":
            j += 1
        tokens.append(("WORD", text[i:j], line))
        i = j
    return tokens


def _parse_block(tokens: List[Tuple[str, str, int]], pos: int, stop_ch: Optional[str] = None) -> Tuple[List[Any], int]:
    items: List[Any] = []
    n = len(tokens)
    while pos < n:
        kind, val, ln = tokens[pos]
        if stop_ch and kind == stop_ch:
            return items, pos + 1
        if kind in ("}", "]"):
            return items, pos + 1

        if kind == "{" or kind == "[":
            sub, pos = _parse_block(tokens, pos + 1, "}" if kind == "{" else "]")
            items.append((kind, sub, ln))
            continue

        # Read key (handling `SectionDevice."Speaker" { ... }`, `False.SectionDevice."Mic1" { ... }`, etc.)
        key_parts = [val]
        pos += 1
        while pos < n and key_parts[-1].endswith(".") and tokens[pos][0] in ("STR", "WORD"):
            key_parts[-1] = key_parts[-1][:-1]
            key_parts.append(tokens[pos][1])
            pos += 1

        key = ".".join(p for p in key_parts if p != "")
        if pos < n and tokens[pos][0] in ("{", "["):
            br = tokens[pos][0]
            sub, pos = _parse_block(tokens, pos + 1, "}" if br == "{" else "]")
            items.append(("BLOCK" if br == "{" else "ARRAY", key, sub, ln))
        elif pos < n and tokens[pos][0] in ("STR", "WORD"):
            # Scalar or sequence pair (e.g., `cset "name='...' on"`, `Syntax 6`, `PlaybackPriority 100`)
            v_kind, v_val, _ = tokens[pos]
            pos += 1
            items.append(("KV", key, v_val, ln))
        else:
            items.append(("ATOM", key, ln))
    return items, pos


def parse_ucm_ast(text: str) -> List[Any]:
    tokens = _tokenize_ucm(text)
    items, _ = _parse_block(tokens, 0, None)
    return items


# ---------------------------------------------------------------------------
# Variable Substitution & Condition Evaluation
# ---------------------------------------------------------------------------

def _expand_vars(s: str, ctx: Dict[str, str]) -> str:
    if "${" not in s:
        return s

    def repl(m: re.Match[str]) -> str:
        expr = m.group(1)
        if expr.startswith("var:"):
            vname = expr[4:]
            if vname.startswith("$"):
                vname = ctx.get(vname[1:], "")
            return ctx.get(vname, "")
        return ctx.get(expr, m.group(0))

    out = s
    for _ in range(3):
        nxt = re.sub(r"\$\{([^}]+)\}", repl, out)
        if nxt == out:
            break
        out = nxt
    return out


def _eval_condition(cond_items: List[Any], ctx: Dict[str, str], snap: Optional[MachineAudioSnapshot]) -> bool:
    kv: Dict[str, str] = {}
    for item in cond_items:
        if item[0] == "KV":
            kv[item[1]] = _expand_vars(item[2], ctx)

    ctype = kv.get("Type", "")
    if ctype == "AlwaysTrue":
        return True
    if ctype == "String":
        if "Empty" in kv:
            return len(kv["Empty"]) == 0
        if "String1" in kv and "String2" in kv:
            return kv["String1"] == kv["String2"]
        if "Haystack" in kv and "Needle" in kv:
            return kv["Needle"] in kv["Haystack"]
        return False
    if ctype == "RegexMatch":
        pat = kv.get("Regex", "")
        s = kv.get("String", "")
        try:
            return re.search(pat, s) is not None
        except re.error:
            return False
    if ctype == "ControlExists":
        ctl_expr = kv.get("Control", "")
        m = re.search(r"name='([^']+)'", ctl_expr)
        cname = m.group(1) if m else ctl_expr.strip("'\"")
        if snap is None:
            return False
        return cname in snap.kcontrols_by_name
    return False


# ---------------------------------------------------------------------------
# AST Walker -> UcmProfile
# ---------------------------------------------------------------------------

def _parse_seq_items(sub: List[Any], ctx: Dict[str, str]) -> List[UcmSequenceCmd]:
    cmds: List[UcmSequenceCmd] = []
    for item in sub:
        if item[0] == "KV":
            cmds.append(UcmSequenceCmd(cmd=item[1], arg=_expand_vars(item[2], ctx)))
        elif item[0] == "ATOM":
            cmds.append(UcmSequenceCmd(cmd=item[1], arg=""))
    return cmds


def _parse_str_list(sub: List[Any], ctx: Dict[str, str]) -> List[str]:
    out: List[str] = []
    for item in sub:
        if item[0] == "ATOM":
            out.append(_expand_vars(item[1], ctx))
        elif item[0] == "KV":
            out.append(_expand_vars(f"{item[1]} {item[2]}".strip(), ctx))
    return out


def _apply_value_items(values: Dict[str, str], sub: List[Any], ctx: Dict[str, str], snap: Optional[MachineAudioSnapshot]) -> None:
    for item in sub:
        if item[0] == "KV":
            values[item[1]] = _expand_vars(item[2], ctx)
        elif item[0] == "BLOCK" and item[1].startswith("If."):
            _walk_if_block(item[2], ctx, snap, lambda branch: _apply_value_items(values, branch, ctx, snap))


def _walk_if_block(
    sub: List[Any],
    ctx: Dict[str, str],
    snap: Optional[MachineAudioSnapshot],
    apply_fn: Any,
) -> None:
    cond_items: List[Any] = []
    true_items: List[Any] = []
    false_items: List[Any] = []
    for ch in sub:
        if ch[0] == "BLOCK" and ch[1] == "Condition":
            cond_items = ch[2]
        elif ch[0] == "BLOCK" and ch[1] == "True":
            true_items = ch[2]
        elif ch[0] == "BLOCK" and ch[1] == "False":
            false_items = ch[2]
        elif ch[0] == "BLOCK" and ch[1].startswith("True."):
            sub_k = ch[1][len("True."):]
            true_items.append(("BLOCK", sub_k, ch[2], ch[3]))
        elif ch[0] == "BLOCK" and ch[1].startswith("False."):
            sub_k = ch[1][len("False."):]
            false_items.append(("BLOCK", sub_k, ch[2], ch[3]))

    res = _eval_condition(cond_items, ctx, snap)
    branch = true_items if res else false_items
    if branch:
        apply_fn(branch)


def _resolve_include_path(ucm_root: str, cur_file_dir: str, ref: str) -> str:
    if ref.startswith("/"):
        return posixpath.normpath(posixpath.join(ucm_root, ref.lstrip("/")))
    return posixpath.normpath(posixpath.join(cur_file_dir, ref))


def _apply_ucm_items(
    items: List[Any],
    profile: UcmProfile,
    ctx: Dict[str, str],
    snap: Optional[MachineAudioSnapshot],
    transport: Optional[Transport],
    cur_file_path: str,
    cur_verb: Optional[UcmVerb] = None,
    cur_device: Optional[UcmSectionDevice] = None,
    is_conditional: bool = False,
) -> None:
    cur_dir = posixpath.dirname(cur_file_path)
    for item in items:
        kind = item[0]
        if kind == "KV":
            key, val = item[1], _expand_vars(item[2], ctx)
            if key == "Syntax":
                try:
                    profile.syntax_version = int(val)
                except ValueError:
                    pass
            elif key == "Comment":
                if cur_device is not None:
                    cur_device.comment = val
                elif cur_verb is not None:
                    cur_verb.comment = val
                else:
                    profile.comment = val
            elif key.startswith("Define."):
                dname = key[len("Define."):]
                ctx[dname] = val
                profile.defines[dname] = val
            elif key.startswith("Value."):
                vname = key[len("Value."):]
                if cur_device is not None:
                    cur_device.values[vname] = val
                elif cur_verb is not None:
                    cur_verb.values[vname] = val
            elif key.startswith("Include.") and key.endswith(".File"):
                inc_id = key[len("Include."):-len(".File")]
                inc_path = _resolve_include_path(profile.ucm_root_dir, cur_dir, val)
                profile.includes.append((inc_id, val))
                _load_and_walk_file(inc_path, profile, ctx, snap, transport, cur_verb=cur_verb)

        elif kind == "ARRAY":
            key, sub = item[1], item[2]
            if key == "BootSequence":
                profile.boot_seq.extend(_parse_seq_items(sub, ctx))
            elif key == "FixedBootSequence":
                profile.fixed_boot_seq.extend(_parse_seq_items(sub, ctx))
            elif key == "EnableSequence":
                cmds = _parse_seq_items(sub, ctx)
                if cur_device is not None:
                    cur_device.enable_seq.extend(cmds)
                elif cur_verb is not None:
                    cur_verb.enable_seq.extend(cmds)
            elif key == "DisableSequence":
                cmds = _parse_seq_items(sub, ctx)
                if cur_device is not None:
                    cur_device.disable_seq.extend(cmds)
                elif cur_verb is not None:
                    cur_verb.disable_seq.extend(cmds)
            elif key == "ConflictingDevice" and cur_device is not None:
                cur_device.conflicting_devices.extend(_parse_str_list(sub, ctx))
            elif key == "SupportedDevice" and cur_device is not None:
                cur_device.supported_devices.extend(_parse_str_list(sub, ctx))

        elif kind == "BLOCK":
            key, sub = item[1], item[2]
            if key == "Define":
                for ch in sub:
                    if ch[0] == "KV":
                        v = _expand_vars(ch[2], ctx)
                        ctx[ch[1]] = v
                        profile.defines[ch[1]] = v
            elif key == "DefineRegex":
                for ch in sub:
                    if ch[0] == "BLOCK":
                        dname = ch[1]
                        dkv = {c[1]: _expand_vars(c[2], ctx) for c in ch[2] if c[0] == "KV"}
                        pat = dkv.get("Regex", "")
                        src_s = dkv.get("String", "")
                        try:
                            m = re.search(pat, src_s)
                            if m:
                                ctx[dname] = m.group(0)
                                for gi, gval in enumerate(m.groups(), start=1):
                                    if gval is not None:
                                        ctx[f"{dname}{gi}"] = gval
                        except re.error:
                            pass
            elif key.startswith("Include."):
                dkv = {c[1]: _expand_vars(c[2], ctx) for c in sub if c[0] == "KV"}
                if "File" in dkv:
                    inc_id = key[len("Include."):]
                    inc_path = _resolve_include_path(profile.ucm_root_dir, cur_dir, dkv["File"])
                    profile.includes.append((inc_id, dkv["File"]))
                    _load_and_walk_file(inc_path, profile, ctx, snap, transport, cur_verb=cur_verb)
            elif key.startswith("SectionUseCase."):
                vname = _expand_vars(key.split(".", 1)[1], ctx)
                dkv = {c[1]: _expand_vars(c[2], ctx) for c in sub if c[0] == "KV"}
                vfile = _resolve_include_path(profile.ucm_root_dir, cur_dir, dkv.get("File", ""))
                verb = profile.verbs.setdefault(
                    vname,
                    UcmVerb(name=vname, file_path=vfile, comment=dkv.get("Comment", "")),
                )
                if dkv.get("File"):
                    _load_and_walk_file(vfile, profile, ctx, snap, transport, cur_verb=verb)
            elif key == "SectionVerb" and cur_verb is not None:
                _apply_ucm_items(sub, profile, ctx, snap, transport, cur_file_path, cur_verb=cur_verb, is_conditional=is_conditional)
            elif key.startswith("SectionDevice.") and cur_verb is not None:
                dname = _expand_vars(key.split(".", 1)[1], ctx)
                dev = cur_verb.devices.setdefault(
                    dname,
                    UcmSectionDevice(name=dname, source_file=cur_file_path, is_conditional=is_conditional),
                )
                _apply_ucm_items(sub, profile, ctx, snap, transport, cur_file_path, cur_verb=cur_verb, cur_device=dev, is_conditional=is_conditional)
            elif key.startswith("SectionModifier.") and cur_verb is not None:
                mname = _expand_vars(key.split(".", 1)[1], ctx)
                mod = cur_verb.modifiers.setdefault(
                    mname,
                    UcmSectionModifier(name=mname, source_file=cur_file_path),
                )
                _apply_modifier_items(sub, mod, ctx)
            elif key == "Value":
                if cur_device is not None:
                    _apply_value_items(cur_device.values, sub, ctx, snap)
                elif cur_verb is not None:
                    _apply_value_items(cur_verb.values, sub, ctx, snap)
            elif key.startswith("If.") or key == "If":
                _walk_if_block(
                    sub,
                    ctx,
                    snap,
                    lambda branch: _apply_ucm_items(
                        branch,
                        profile,
                        ctx,
                        snap,
                        transport,
                        cur_file_path,
                        cur_verb=cur_verb,
                        cur_device=cur_device,
                        is_conditional=True,
                    ),
                )


def _apply_modifier_items(sub: List[Any], mod: UcmSectionModifier, ctx: Dict[str, str]) -> None:
    for item in sub:
        if item[0] == "KV":
            if item[1] == "Comment":
                mod.comment = _expand_vars(item[2], ctx)
            elif item[1].startswith("Value."):
                mod.values[item[1][6:]] = _expand_vars(item[2], ctx)
        elif item[0] == "ARRAY":
            if item[1] == "EnableSequence":
                mod.enable_seq.extend(_parse_seq_items(item[2], ctx))
            elif item[1] == "DisableSequence":
                mod.disable_seq.extend(_parse_seq_items(item[2], ctx))
            elif item[1] == "SupportedDevice":
                mod.supported_devices.extend(_parse_str_list(item[2], ctx))
            elif item[1] == "ConflictingDevice":
                mod.conflicting_devices.extend(_parse_str_list(item[2], ctx))
        elif item[0] == "BLOCK" and item[1] == "Value":
            _apply_value_items(mod.values, item[2], ctx, None)


def _load_and_walk_file(
    abs_path: str,
    profile: UcmProfile,
    ctx: Dict[str, str],
    snap: Optional[MachineAudioSnapshot],
    transport: Optional[Transport],
    cur_verb: Optional[UcmVerb] = None,
) -> None:
    if not abs_path or not transport:
        return
    rel = posixpath.relpath(abs_path, profile.ucm_root_dir) if profile.ucm_root_dir else abs_path
    if rel in profile.raw_files:
        return
    txt = transport.read_text(abs_path)
    if txt is None:
        profile.eval_notes.append(f"Skipped/unreadable include: {abs_path}")
        return
    profile.raw_files[rel] = txt
    ast = parse_ucm_ast(txt)
    _apply_ucm_items(ast, profile, ctx, snap, transport, abs_path, cur_verb=cur_verb)


# ---------------------------------------------------------------------------
# Card UCM2 Profile Discovery & Loading
# ---------------------------------------------------------------------------

def build_ucm_eval_context(snap: MachineAudioSnapshot) -> Dict[str, str]:
    c = snap.card
    if not c:
        return {}
    return {
        "CardNumber": str(c.card_id),
        "CardId": c.id or f"card{c.card_id}",
        "CardName": c.name or c.id,
        "CardLongName": c.longname or c.name,
        "CardDriver": c.driver,
        "CardComponents": c.components,
        "OpenName": f"hw:{c.card_id}",
        "LibDir": "/var/lib/alsa",
    }


def find_ucm_master_path(
    transport: Transport,
    snap: MachineAudioSnapshot,
    ucm_root: str = "/usr/share/alsa/ucm2",
) -> Optional[str]:
    """Locate the master UCM2 config file matching ucm.conf search rules."""
    c = snap.card
    if not c:
        return None

    candidates: List[str] = []
    if c.driver:
        if c.longname:
            candidates.append(posixpath.join(ucm_root, "conf.d", c.driver, f"{c.longname}.conf"))
            candidates.append(posixpath.join(ucm_root, c.driver, f"{c.longname}.conf"))
        candidates.append(posixpath.join(ucm_root, "conf.d", c.driver, f"{c.driver}.conf"))
        candidates.append(posixpath.join(ucm_root, c.driver, f"{c.driver}.conf"))
    if c.name:
        candidates.append(posixpath.join(ucm_root, "conf.d", c.name, f"{c.name}.conf"))

    for cand in candidates:
        txt = transport.read_text(cand)
        if txt is not None:
            return cand
    return None


def load_ucm_profile(
    transport: Transport,
    snap: MachineAudioSnapshot,
    ucm_root: str = "/usr/share/alsa/ucm2",
    master_path_override: Optional[str] = None,
) -> Optional[UcmProfile]:
    """Load and evaluate the UCM2 profile for the card in `snap`."""
    master = master_path_override or find_ucm_master_path(transport, snap, ucm_root)
    if not master:
        # Try asking alsaucm directly in case of symlink / custom ALSA config
        if snap.card:
            rc, out, _ = run_alsaucm_command(transport, snap.card.card_id, None, ["dump", "text"])
            if rc == 0 and out.strip():
                return parse_alsaucm_dump_text(out, snap, ucm_root)
        return None

    c = snap.card
    rel_master = posixpath.relpath(master, ucm_root) if master.startswith(ucm_root) else posixpath.basename(master)
    profile = UcmProfile(
        card_name=c.name if c else "",
        card_longname=c.longname if c else "",
        card_driver=c.driver if c else "",
        ucm_root_dir=ucm_root,
        master_rel_path=rel_master,
    )
    ctx = build_ucm_eval_context(snap)
    _load_and_walk_file(master, profile, ctx, snap, transport)
    return profile


def parse_alsaucm_dump_text(dump_txt: str, snap: MachineAudioSnapshot, ucm_root: str) -> UcmProfile:
    """Parse `alsaucm -c hw:N dump text` output into a UcmProfile."""
    c = snap.card
    profile = UcmProfile(
        card_name=c.name if c else "",
        card_longname=c.longname if c else "",
        card_driver=c.driver if c else "",
        ucm_root_dir=ucm_root,
        master_rel_path=f"{c.driver}/{c.longname or c.driver}.conf" if c else "card.conf",
    )
    profile.raw_files["alsaucm_dump.conf"] = dump_txt
    ast = parse_ucm_ast(dump_txt)
    ctx = build_ucm_eval_context(snap)
    for item in ast:
        if item[0] == "BLOCK" and item[1].startswith("Verb."):
            vname = item[1][5:]
            verb = UcmVerb(name=vname)
            profile.verbs[vname] = verb
            _apply_ucm_items(item[2], profile, ctx, snap, None, "alsaucm_dump.conf", cur_verb=verb)
        elif item[0] == "BLOCK" and item[1].startswith("Device."):
            # Top-level fallback
            pass
    return profile
