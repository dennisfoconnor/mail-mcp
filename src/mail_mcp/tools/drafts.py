"""Draft creation tools — the preferred write path.

Creating a draft is the safest mutating operation offered by this server: the
message lands in the user's Drafts mailbox where a human reviews and sends it
from their own email client. This fork has no send tool at all, so a draft
is the only thing this server can ever write towards another person.
"""

from __future__ import annotations

from .. import imap_client, smtp_client
from ..config import AccountModel, Config
from ..credentials import resolve_auth
from ..safety.attachments import resolve_many
from ..safety.validation import ValidationError
from ..signatures import sign_body
from .schemas import (
    ForwardDraftInput,
    ReplyDraftInput,
    SaveDraftInput,
    UpdateDraftInput,
)

_SIGNATURE_STILL_PRESENT_NOTE = (
    "include_signature=false: mail-mcp did not add the signature, but the body "
    "you passed already contains the account's signature text (possibly inside "
    "quoted material). Remove it from your own text if the message must go "
    "without it."
)


def _signature_fields(status: str) -> dict:
    fields = {"signature": status}
    if status == "still_present":
        fields["signature_note"] = _SIGNATURE_STILL_PRESENT_NOTE
    return fields


_HTML_IN_BODY_WARNING = (
    "body looks like HTML but the message was built as text/plain — the "
    "recipient will see raw markup. Pass the HTML in body_html (and a "
    "plain-text version in body) to have it rendered."
)


def _drafts_mailbox_strict(
    client, account: AccountModel, override: str | None, *, tool: str,
) -> str:
    """Resolve the drafts mailbox and refuse caller-supplied non-drafts overrides.

    ``update_draft`` ends with a permanent delete of the source UID.
    Without this check, a caller (or a prompt-injected model on a
    default-visible draft tool) could pass
    ``mailbox="INBOX"`` plus an arbitrary UID and the handler would
    happily APPEND a copy to Drafts and then UID-expunge the original
    from INBOX — bypassing ``MAIL_MCP_WRITE_ENABLED``,
    ``MAIL_MCP_ALLOW_PERMANENT_DELETE``, and the per-call ``confirm=true``
    that ``delete_emails`` requires for the same primitive. That is a
    trust-boundary violation in a tool the user expects to operate
    only on drafts.

    The strict resolution: always derive the drafts mailbox from server
    state (SPECIAL-USE \\Drafts → configured value → localised list).
    If the caller provided ``override``, it must equal that resolved
    name; anything else raises :class:`ValidationError` with a clear
    explanation. Pre-flight fails before any IMAP fetch / append /
    delete, so a rejected call mutates nothing.
    """
    resolved = imap_client.resolve_drafts_mailbox(client, account)
    if override is not None and override != resolved:
        raise ValidationError(
            f"{tool} only operates on the account's drafts mailbox. "
            f"Resolved drafts mailbox is {resolved!r}; got "
            f"mailbox={override!r}. Pass mailbox=None (the default) to "
            "let the server-side SPECIAL-USE detection pick the right "
            "folder, or pass exactly the resolved name above."
        )
    return resolved


def save_draft(cfg: Config, params: SaveDraftInput) -> dict:
    acct = cfg.account(params.account)
    creds = resolve_auth(acct)
    attachments = resolve_many(params.attachments) if params.attachments else []
    signed = sign_body(cfg, acct, params.include_signature, params.body, params.body_html)
    msg = smtp_client.build_message(
        from_addr=acct.email,
        to=params.to,
        cc=params.cc,
        subject=params.subject,
        body_text=signed.text,
        in_reply_to=params.in_reply_to,
        references=params.references,
        attachments=attachments,
        body_html=signed.html,
    )
    # BCC is deliberately not persisted on a draft: the user's mail client
    # will re-enter BCC at send time. Drafts with BCC headers break some
    # providers' threading.
    with imap_client.connect(acct, creds) as c:
        drafts_mailbox, draft_uid = imap_client.save_draft(
            c, account=acct, message_bytes=bytes(msg),
        )
    response = {
        "account": acct.alias,
        "mailbox": drafts_mailbox,
        "uid": int(draft_uid),
        "message_id": msg["Message-ID"],
        # Derived from the resolved attachments actually attached, not the
        # raw input, so a caller can verify what was actually attached.
        "attachments": [
            {"filename": a.filename, "size": a.size, "content_type": a.content_type}
            for a in attachments
        ],
    }
    if params.bcc:
        # BCC is intentionally not persisted on a draft (see above). Surface
        # that explicitly so the caller never assumes the BCC was stored.
        response["bcc_dropped"] = list(params.bcc)
        response["note"] = (
            "BCC was not persisted on the draft. Tell the user to re-enter "
            "it in their mail client when they send."
        )
    if not params.body_html and smtp_client.looks_like_html(params.body):
        response["html_warning"] = _HTML_IN_BODY_WARNING
    response.update(_signature_fields(signed.status))
    return response


def reply_draft(cfg: Config, params: ReplyDraftInput) -> dict:
    acct = cfg.account(params.account)
    # Sign the caller's text BEFORE the builder appends the attribution quote,
    # so the signature sits between the reply and the quote — and before
    # connecting, so an undecided signature (SIGNATURE_CHOICE_REQUIRED) costs
    # nothing.
    signed = sign_body(cfg, acct, params.include_signature, params.body, params.body_html)
    creds = resolve_auth(acct)
    with imap_client.connect(acct, creds) as c:
        _raw, headers = imap_client.fetch_raw_message(
            c, mailbox=params.mailbox, uid=params.uid,
        )
        msg = smtp_client.build_reply_message(
            from_addr=acct.email,
            original_headers=headers,
            body_text=signed.text,
            extra_to=params.extra_to,
            cc=params.cc,
            reply_all=params.reply_all,
            include_original_quote=params.include_original_quote,
            body_html=signed.html,
        )
        drafts_mailbox, draft_uid = imap_client.save_draft(
            c, account=acct, message_bytes=bytes(msg),
        )
    response = {
        "account": acct.alias,
        "mailbox": drafts_mailbox,
        "uid": int(draft_uid),
        "message_id": msg["Message-ID"],
        "in_reply_to": msg.get("In-Reply-To"),
        "subject": msg.get("Subject"),
        **_signature_fields(signed.status),
    }
    if not params.body_html and smtp_client.looks_like_html(params.body):
        response["html_warning"] = _HTML_IN_BODY_WARNING
    return response


def update_draft(cfg: Config, params: UpdateDraftInput) -> dict:
    """Replace a draft in place via APPEND-then-DELETE.

    IMAP has no UPDATE. The safe ordering is: build the new message, APPEND
    it (new UID), only then mark the old UID deleted and expunge. A failure
    in the APPEND step leaves the original draft untouched; a failure in the
    delete step leaves a harmless duplicate rather than data loss.

    Attachment semantics:

    * ``attachments`` omitted (``None``) — the original draft's attachments
      are carried over unchanged. This matches the implicit "preserve"
      behaviour that ``preserve_message_id`` / ``in_reply_to`` already use
      for header-bound fields.
    * ``attachments=[]`` — explicitly clear all attachments.
    * ``attachments=[spec, ...]`` — replace the attachment set with the
      supplied list.
    """
    import email as _email
    import email.policy as _policy

    acct = cfg.account(params.account)
    creds = resolve_auth(acct)
    with imap_client.connect(acct, creds) as c:
        mailbox = _drafts_mailbox_strict(c, acct, params.mailbox, tool="update_draft")
        raw, headers = imap_client.fetch_raw_message(
            c, mailbox=mailbox, uid=params.uid,
        )
        original = _email.message_from_bytes(raw, policy=_policy.default)
        if params.to is not None:
            new_to = params.to
        else:
            # getaddresses, not split(","): preserve recipients whose display
            # name contains a comma when carrying them over from the draft.
            new_to = imap_client._header_addresses(original.get("To", ""))
        if params.cc is not None:
            new_cc = params.cc
        else:
            extracted_cc = imap_client._header_addresses(original.get("Cc", ""))
            new_cc = extracted_cc or None
        new_subject = params.subject if params.subject is not None else original.get("Subject", "")
        if params.include_signature is not None and params.body is None:
            raise ValidationError(
                "include_signature only applies when you pass body (a preserved body "
                "is left exactly as it was). To add the signature, pass the draft's "
                "body (from get_email) with include_signature=true; to remove it, pass "
                "the body with the signature deleted and include_signature=false."
            )
        if params.body_html and params.body is None:
            raise ValidationError(
                "body_html requires body in the same call: body is the "
                "plain-text alternative of the multipart/alternative pair. "
                "Omit both to preserve the original draft's body."
            )
        # Default to a plain-text body. When preserving the original body
        # (params.body is None), keep whatever the draft actually had: both
        # alternatives of a multipart/alternative draft (as produced by
        # save_draft with body_html), an HTML-only body (common from Outlook /
        # Apple Mail / Thunderbird), or plain text. Anything less silently
        # loses content — append-then-delete makes that loss permanent.
        new_body_subtype = "plain"
        new_body_html = params.body_html
        signature_status = None
        if params.body is not None:
            # A replaced body is a freshly written message: sign it like
            # save_draft would (idempotent, so a body read back from a signed
            # draft is not signed twice). A preserved body is left untouched.
            # In "ask" mode an undecided replacement is rejected like any other
            # write: the decision is the user's, never inferred from the old
            # draft (its content cannot tell a declined signature from one
            # inside quoted text). The agent passes the choice the user made.
            signed = sign_body(cfg, acct, params.include_signature, params.body, params.body_html)
            new_body, new_body_html = signed.text, signed.html
            signature_status = signed.status
        else:
            # _safe_get_content, never .get_content(): drafts written by other
            # clients can declare unknown/malformed charsets (LookupError),
            # and a crash here would make the draft un-updatable forever.
            preserved_plain = original.get_body(preferencelist=("plain",))
            preserved_html = original.get_body(preferencelist=("html",))
            if preserved_plain is not None:
                new_body = imap_client._safe_get_content(preserved_plain)
                if preserved_html is not None:
                    new_body_html = imap_client._safe_get_content(preserved_html)
            elif preserved_html is not None:
                new_body = imap_client._safe_get_content(preserved_html)
                new_body_subtype = "html"
            else:
                new_body = ""
        in_reply_to = params.in_reply_to if params.in_reply_to is not None else original.get("In-Reply-To")
        references = params.references if params.references is not None else (
            original.get("References", "").split() or None
        )
        new_attachments = (
            resolve_many(params.attachments) if params.attachments else []
        )
        msg = smtp_client.build_message(
            from_addr=acct.email,
            to=new_to,
            cc=new_cc,
            subject=new_subject,
            body_text=new_body,
            in_reply_to=in_reply_to,
            references=references,
            attachments=new_attachments,
            body_subtype=new_body_subtype,
            body_html=new_body_html,
        )
        if params.attachments is None:
            # Preserve the original's attachments — caller did not opt in to
            # a replacement set. Empty list means "explicitly clear", which
            # is honoured by passing ``[]`` to ``resolve_many`` above.
            smtp_client.carry_over_attachments(original, msg)
        if params.preserve_message_id and original.get("Message-ID"):
            del msg["Message-ID"]
            msg["Message-ID"] = original["Message-ID"]
        new_drafts_mailbox, new_uid = imap_client.save_draft(
            c, account=acct, message_bytes=bytes(msg),
        )
        warning = _delete_old_draft_uid_safely(
            c, mailbox=mailbox, uid=params.uid, trash_mailbox=acct.trash_mailbox,
        )
    response = {
        "account": acct.alias,
        "mailbox": new_drafts_mailbox,
        "old_uid": params.uid,
        "new_uid": int(new_uid),
        "message_id": msg["Message-ID"],
    }
    if signature_status is not None:
        response.update(_signature_fields(signature_status))
    if warning:
        response["warning"] = warning
    return response


def _delete_old_draft_uid_safely(
    client, *, mailbox: str, uid: int, trash_mailbox: str,
) -> str | None:
    """Delete the previous draft UID, falling back to mark-deleted on no-UIDPLUS.

    ``update_draft`` APPENDs a new copy and then has to remove the old UID. Bare ``EXPUNGE`` would risk wiping
    unrelated ``\\Deleted``-flagged messages in the same folder, and the
    safe ``UID EXPUNGE`` requires RFC 4315 UIDPLUS. When the server does
    not advertise UIDPLUS we fall back to flagging the old UID
    ``\\Deleted`` without expunging — the user sees a duplicate the next
    time their mail client re-syncs, which is recoverable; expunging
    other people's messages is not.
    """
    try:
        imap_client.delete_uids(
            client,
            mailbox=mailbox,
            uids=[uid],
            trash_mailbox=trash_mailbox,
            permanent=True,
        )
        return None
    except imap_client.UIDPlusRequired:
        client.select_folder(mailbox, readonly=False)
        client.add_flags([uid], [b"\\Deleted"])
        return (
            f"server does not advertise UIDPLUS; the previous draft "
            f"(uid={uid}) has been flagged \\Deleted but not expunged "
            "to avoid removing unrelated messages another client may "
            "have flagged. Your mail client will hide it on next sync."
        )


def forward_draft(cfg: Config, params: ForwardDraftInput) -> dict:
    acct = cfg.account(params.account)
    creds = resolve_auth(acct)
    signed = sign_body(cfg, acct, params.include_signature, params.comment, params.comment_html)
    with imap_client.connect(acct, creds) as c:
        raw, headers = imap_client.fetch_raw_message(
            c, mailbox=params.mailbox, uid=params.uid,
        )
        msg, _bcc = smtp_client.build_forward_message(
            from_addr=acct.email,
            to=params.to,
            original_headers=headers,
            original_raw=raw,
            comment=signed.text,
            cc=params.cc,
            bcc=params.bcc,
            comment_html=signed.html,
        )
        drafts_mailbox, draft_uid = imap_client.save_draft(
            c, account=acct, message_bytes=bytes(msg),
        )
    response = {
        "account": acct.alias,
        "mailbox": drafts_mailbox,
        "uid": int(draft_uid),
        "message_id": msg["Message-ID"],
        "subject": msg.get("Subject"),
        "attached": "original message attached as message/rfc822",
        **_signature_fields(signed.status),
    }
    if params.bcc:
        # BCC is not persisted on a draft (same as save_draft) — surface that
        # rather than silently dropping it, so the caller knows to re-enter it
        # at send time in their mail client.
        response["bcc_dropped"] = list(params.bcc)
        response["note"] = (
            "BCC was not persisted on the forwarded draft. Tell the user to "
            "re-enter it in their mail client when they send."
        )
    return response
