/**
 * Browser-side WebAuthn / passkey helpers.
 *
 * The backend returns/accepts WebAuthn options + responses in the standard JSON
 * wire shape (base64url for every binary field — challenge, credential ids,
 * public keys, signatures). The browser `navigator.credentials` API, however,
 * works in `ArrayBuffer`s. These helpers convert between the two so the auth
 * store can hand the server JSON straight to `create()` / `get()` and serialise
 * the result back.
 *
 * Passkeys are an ADDITIONAL MFA factor — this module is the separate WebAuthn
 * code path; TOTP / email-OTP flows are untouched.
 */

export function isWebAuthnSupported(): boolean {
	return (
		typeof window !== 'undefined' &&
		typeof window.PublicKeyCredential !== 'undefined' &&
		typeof navigator?.credentials?.create === 'function'
	);
}

function base64urlToBuffer(value: string): ArrayBuffer {
	const padded = value.replace(/-/g, '+').replace(/_/g, '/');
	const pad = padded.length % 4 === 0 ? '' : '='.repeat(4 - (padded.length % 4));
	const binary = atob(padded + pad);
	const bytes = new Uint8Array(binary.length);
	for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
	return bytes.buffer;
}

function bufferToBase64url(buffer: ArrayBuffer): string {
	const bytes = new Uint8Array(buffer);
	let binary = '';
	for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
	return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/**
 * Run a passkey REGISTRATION ceremony. `options` is the server's
 * `WebAuthnRegisterStartResponse.options` (PublicKeyCredentialCreationOptions in
 * JSON form). Returns the JSON the backend's `/register/verify` expects.
 */
export async function performRegistration(options: any): Promise<any> {
	const publicKey: PublicKeyCredentialCreationOptions = {
		...options,
		challenge: base64urlToBuffer(options.challenge),
		user: { ...options.user, id: base64urlToBuffer(options.user.id) },
		excludeCredentials: (options.excludeCredentials ?? []).map((c: any) => ({
			...c,
			id: base64urlToBuffer(c.id),
		})),
	};
	const cred = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential;
	if (!cred) throw new Error('Passkey registration was cancelled');
	const response = cred.response as AuthenticatorAttestationResponse;
	return {
		id: cred.id,
		rawId: bufferToBase64url(cred.rawId),
		type: cred.type,
		response: {
			clientDataJSON: bufferToBase64url(response.clientDataJSON),
			attestationObject: bufferToBase64url(response.attestationObject),
			transports:
				typeof response.getTransports === 'function' ? response.getTransports() : [],
		},
		clientExtensionResults: cred.getClientExtensionResults(),
	};
}

/**
 * Run a passkey AUTHENTICATION ceremony. `options` is the server's
 * `WebAuthnAuthStartResponse.options` (PublicKeyCredentialRequestOptions in JSON
 * form). Returns the JSON the backend's `/authenticate/verify` expects.
 */
export async function performAuthentication(options: any): Promise<any> {
	const publicKey: PublicKeyCredentialRequestOptions = {
		...options,
		challenge: base64urlToBuffer(options.challenge),
		allowCredentials: (options.allowCredentials ?? []).map((c: any) => ({
			...c,
			id: base64urlToBuffer(c.id),
		})),
	};
	const cred = (await navigator.credentials.get({ publicKey })) as PublicKeyCredential;
	if (!cred) throw new Error('Passkey authentication was cancelled');
	const response = cred.response as AuthenticatorAssertionResponse;
	return {
		id: cred.id,
		rawId: bufferToBase64url(cred.rawId),
		type: cred.type,
		response: {
			clientDataJSON: bufferToBase64url(response.clientDataJSON),
			authenticatorData: bufferToBase64url(response.authenticatorData),
			signature: bufferToBase64url(response.signature),
			userHandle: response.userHandle ? bufferToBase64url(response.userHandle) : null,
		},
		clientExtensionResults: cred.getClientExtensionResults(),
	};
}
