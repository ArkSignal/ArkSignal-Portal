from flask import flash, jsonify, redirect, render_template, request, url_for


def action_response(ok, message, *, redirect_endpoint, redirect_kwargs=None, fragment_template=None, fragment_context=None):
    """Answer an admin action as JSON (in-page submit) or flash + redirect (plain form submit)."""
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        payload = {"ok": ok, "message": message}
        if fragment_template:
            payload["html"] = render_template(fragment_template, **(fragment_context or {}))
        return jsonify(payload), (200 if ok else 400)
    flash(message, "success" if ok else "error")
    return redirect(url_for(redirect_endpoint, **(redirect_kwargs or {})))
