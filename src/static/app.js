/* jshint esversion: 11, browser: true */
/* globals google */

"use strict";

// Browser side of the machines app: the sign-in page (index.html, following
// gcp-myworld's) and the copy buttons of the machines page.

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
	const init = { method: options.method || "GET", headers: {} };
	if (options.body !== undefined) {
		init.headers["Content-Type"] = "application/json";
		init.body = JSON.stringify(options.body);
	}
	const response = await fetch(path, init);
	const data = await response.json();
	if (!response.ok) {
		throw new Error(data.error || response.statusText);
	}
	return data;
}

function showError(message) {
	const box = $("error");
	box.textContent = message;
	box.hidden = !message;
}

// ─── footer (both pages) ────────────────────────────────────────────────────

const REPO = "veltzer/gcp-machines";

function link(href, ...children) {
	const a = document.createElement("a");
	a.href = href;
	a.append(...children);
	return a;
}

// The footer's provenance, as gcp-myworld's: the year and the commit come
// from the deploy stamp, so the same deploy always says the same.
async function showBuild() {
	const build = $("build");
	if (!build) {
		return;
	}
	try {
		const info = await api("/app/version");
		const deployed = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(info.deploy_date || "") ? info.deploy_date : null;
		for (const year of document.querySelectorAll(".copyright-year")) {
			year.textContent = (deployed || new Date().toISOString()).slice(0, 4);
		}
		const commit = (info.git_describe || "").match(/(?:-g)?([0-9a-f]{7,40})(?:-dirty)?$/);
		build.append("Built");
		if (commit) {
			const code = document.createElement("code");
			code.textContent = commit[1];
			build.append(
				" from ",
				link(`https://github.com/${REPO}`, REPO),
				" commit ",
				link(`https://github.com/${REPO}/commit/${commit[1]}`, code),
			);
		}
		if (deployed) {
			const time = document.createElement("time");
			time.dateTime = deployed;
			time.textContent = `${deployed.slice(0, 16).replace("T", " ")} UTC`;
			build.append(" on ", time);
		}
		build.append(info.revision && info.revision !== "local" ? `, serving as ${info.revision}` : " (local)");
	} catch {
		// cosmetic only
	}
}

// ─── sign-in page ───────────────────────────────────────────────────────────

function loadGoogleSignIn(clientId) {
	const script = document.createElement("script");
	script.src = "https://accounts.google.com/gsi/client";
	script.async = true;
	script.onload = () => {
		google.accounts.id.initialize({
			client_id: clientId,
			ux_mode: "redirect",
			login_uri: `${window.location.origin}/auth/login`,
		});
		google.accounts.id.renderButton($("signin"), {
			type: "standard",
			size: "large",
			theme: "outline",
			text: "continue_with",
			shape: "pill",
			logo_alignment: "left",
			width: Math.min(360, $("signin").clientWidth || 360),
		});
		$("signin-loading").hidden = true;
	};
	script.onerror = () => {
		$("signin-loading").hidden = true;
		showError("Could not load Google sign-in. Check your connection and reload.");
	};
	document.head.appendChild(script);
}

function setupProviders(configured) {
	for (const link of document.querySelectorAll(".provider[data-provider]")) {
		if (configured[link.dataset.provider]) {
			continue;
		}
		link.classList.add("off");
		link.setAttribute("aria-disabled", "true");
		link.title = "Not configured on this server";
		link.onclick = (event) => event.preventDefault();
	}
}

function setupEmailForm(minLength) {
	const form = $("email-form");
	const password = form.elements.password;
	const submit = $("email-submit");
	let mode = "login";

	const applyMode = () => {
		const signup = mode === "register";
		submit.textContent = signup ? "Create account" : "Sign in";
		$("email-switch-text").textContent = signup ? "Already have an account?" : "New here?";
		$("email-mode").textContent = signup ? "Sign in" : "Create an account";
		password.autocomplete = signup ? "new-password" : "current-password";
		password.minLength = signup ? minLength : 0;
		showError("");
	};
	$("email-mode").onclick = () => {
		mode = mode === "login" ? "register" : "login";
		applyMode();
	};
	$("toggle-password").onclick = () => {
		const hidden = password.type === "password";
		password.type = hidden ? "text" : "password";
		$("toggle-password").textContent = hidden ? "Hide" : "Show";
	};
	form.onsubmit = async (event) => {
		event.preventDefault();
		showError("");
		const email = form.elements.email.value.trim();
		if (!email || !email.includes("@")) {
			showError("Enter your email address.");
			form.elements.email.focus();
			return;
		}
		if (mode === "register" && password.value.length < minLength) {
			showError(`Use at least ${minLength} characters for the password.`);
			password.focus();
			return;
		}
		submit.disabled = true;
		try {
			const result = await api(`/auth/email/${mode}`, { method: "POST", body: { email, password: password.value } });
			window.location.href = result.next;
		} catch (e) {
			showError(e.message);
			submit.disabled = false;
		}
	};
	applyMode();
}

async function indexPage() {
	const config = await api("/api/config");
	if (config.signed_in) {
		window.location.reload();
		return;
	}
	const error = new URLSearchParams(window.location.search).get("error");
	if (error) {
		showError(error);
		window.history.replaceState(null, "", "/");
	}
	if (config.google_client_id) {
		loadGoogleSignIn(config.google_client_id);
	} else {
		$("signin-loading").hidden = true;
		$("signin-missing").hidden = false;
	}
	setupProviders(config.providers);
	setupEmailForm(config.password_min_length);
	$("dev-login").hidden = !config.dev_login;
}

// ─── machines page ──────────────────────────────────────────────────────────

// The admin's Students table: every assign dropdown offers the machines'
// owner labels (data-owners, comma separated; labels cannot hold commas)
// with the account's current assignment selected.
function fillAssignments() {
	const table = document.querySelector(".students[data-owners]");
	if (!table) {
		return;
	}
	const owners = table.dataset.owners ? table.dataset.owners.split(",") : [];
	for (const select of table.querySelectorAll("select[data-selected]")) {
		for (const owner of owners) {
			const option = document.createElement("option");
			option.value = owner;
			option.textContent = owner;
			option.selected = owner === select.dataset.selected;
			select.appendChild(option);
		}
	}
}

// A running machine's turn-off time arrives in UTC (the server cannot know
// the viewer's time zone); show it in the browser's local time instead.
function localizeTimes() {
	for (const time of document.querySelectorAll("time[datetime]")) {
		time.textContent = new Date(time.dateTime).toLocaleTimeString([], {
			hour: "2-digit",
			minute: "2-digit",
		});
	}
}

// A Delete form (data-delete holds the machine name) goes out only after
// the delete dialog: the name typed in unlocks its button, and only that
// button (or Enter on the matching name) submits the form. Cancel, Escape
// and the backdrop all leave the machine alone.
function confirmDeletes() {
	const dialog = $("delete-dialog");
	if (!dialog) {
		return;
	}
	const input = $("delete-input");
	const confirm = $("delete-confirm");
	let pending = null;
	const matches = () => pending !== null && input.value.trim() === pending.dataset.delete;
	const submit = () => {
		if (!matches()) {
			return;
		}
		const form = pending;
		pending = null;
		dialog.close();
		// submit() skips onsubmit, so this does not open the dialog again
		form.submit();
	};
	input.oninput = () => {
		confirm.disabled = !matches();
	};
	input.onkeydown = (event) => {
		if (event.key === "Enter") {
			event.preventDefault();
			submit();
		}
	};
	confirm.onclick = submit;
	$("delete-cancel").onclick = () => dialog.close();
	dialog.onclick = (event) => {
		if (event.target === dialog) {
			dialog.close();
		}
	};
	dialog.onclose = () => {
		pending = null;
	};
	for (const form of document.querySelectorAll("form[data-delete]")) {
		form.onsubmit = (event) => {
			event.preventDefault();
			pending = form;
			for (const name of dialog.querySelectorAll(".delete-name")) {
				name.textContent = form.dataset.delete;
			}
			input.value = "";
			confirm.disabled = true;
			dialog.showModal();
			input.focus();
		};
	}
}

function machinesPage() {
	fillAssignments();
	localizeTimes();
	confirmDeletes();
	for (const button of document.querySelectorAll("button[data-copy]")) {
		button.onclick = async () => {
			await navigator.clipboard.writeText(button.dataset.copy);
			const original = button.textContent;
			button.textContent = "Copied!";
			setTimeout(() => {
				button.textContent = original;
			}, 2000);
		};
	}
}

document.addEventListener("DOMContentLoaded", () => {
	showBuild();
	const page = document.body.dataset.page;
	if (page === "index") {
		indexPage().catch((e) => showError(e.message));
	} else if (page === "machines") {
		machinesPage();
	}
});
