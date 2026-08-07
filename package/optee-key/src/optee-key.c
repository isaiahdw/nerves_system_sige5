/*
 * optee-key - work with a device key held in OP-TEE's PKCS #11 token.
 *
 * The private key is generated inside the secure world and marked sensitive
 * and non-extractable, so the TA will not hand it back. Signing happens in
 * TrustZone; this program only ever sees signatures and the public key.
 *
 * Drive it from a process of its own. Cryptoki is initialised here for
 * single-threaded use, and one process can hold a session open for as long as
 * the caller needs it - see the serve command.
 *
 * Commands, all writing to stdout:
 *   info                    tokens, and whether each is initialised
 *   init <label>            initialise a token and set its user PIN
 *   generate <label> <k>    generate an EC P-256 keypair as token objects
 *   pubkey <label> <k>      the public key, DER SubjectPublicKeyInfo, hex
 *   sign <label> <k> <digest>  sign a hex digest, DER ECDSA, hex
 *   serve <label> <k>          answer commands on stdin until EOF
 *
 * PINs come from the environment - OPTEE_KEY_PIN, and OPTEE_KEY_SO_PIN for
 * init - not from the command line. /proc/<pid>/cmdline is readable by every
 * process on the system, and serve holds its arguments there for as long as
 * it runs; /proc/<pid>/environ is readable only by the process's own user.
 *
 * Hex in and out so the caller does not have to care about binary framing.
 *
 * serve exists so a caller can hold one process, and so one TEE session, open
 * across many signatures rather than starting a process for each. Each line in
 * is a command, each line out is its answer:
 *
 *   sign <hex digest>  ->  ok <der hex>  |  error <what>
 *   pubkey             ->  ok <spki hex> |  error <what>
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <pkcs11.h>

#define MAX_SLOTS 16

/* prime256v1 */
static const CK_BYTE ec_params[] = {
	0x06, 0x08, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x03, 0x01, 0x07
};

/* SubjectPublicKeyInfo prefix: id-ecPublicKey + prime256v1, uncompressed point */
static const unsigned char spki_prefix[] = {
	0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x02,
	0x01, 0x06, 0x08, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x03, 0x01, 0x07, 0x03,
	0x42, 0x00
};

static CK_FUNCTION_LIST_PTR fn;

static int serve_pubkey(CK_SESSION_HANDLE session, const char *key);
static int serve_sign(CK_SESSION_HANDLE session, const char *key,
		      const char *digest_hex);

#define CHECK(call, what)                                                     \
	do {                                                                  \
		CK_RV _rv = (call);                                           \
		if (_rv != CKR_OK) {                                          \
			fprintf(stderr, "%s: 0x%lx\n", what,                  \
				(unsigned long)_rv);                          \
			return 1;                                             \
		}                                                             \
	} while (0)

static int to_hex(const unsigned char *buf, size_t len, char *out, size_t max)
{
	if (len * 2 + 1 > max)
		return 1;

	for (size_t i = 0; i < len; i++)
		sprintf(out + i * 2, "%02x", buf[i]);

	out[len * 2] = 0;
	return 0;
}

static int get_hex(const char *in, unsigned char *out, size_t max, size_t *len)
{
	size_t n = strlen(in);

	if (n % 2 || n / 2 > max)
		return -1;

	for (size_t i = 0; i < n; i++)
		if (!isxdigit((unsigned char)in[i]))
			return -1;

	for (size_t i = 0; i < n; i += 2) {
		unsigned int byte;

		if (sscanf(in + i, "%2x", &byte) != 1)
			return -1;
		out[i / 2] = (unsigned char)byte;
	}

	*len = n / 2;
	return 0;
}

/* PKCS #11 pads labels with spaces, so compare ignoring the padding */
static int label_is(const CK_UTF8CHAR *padded, size_t len, const char *want)
{
	size_t n = strlen(want);

	if (n > len || memcmp(padded, want, n))
		return 0;

	for (size_t i = n; i < len; i++)
		if (padded[i] != ' ')
			return 0;

	return 1;
}

static int find_token(const char *label, int allow_blank, CK_SLOT_ID *out)
{
	CK_SLOT_ID slots[MAX_SLOTS];
	CK_ULONG count = MAX_SLOTS;
	CK_TOKEN_INFO info;

	if (fn->C_GetSlotList(CK_TRUE, slots, &count) != CKR_OK)
		return -1;

	for (CK_ULONG i = 0; i < count; i++)
		if (fn->C_GetTokenInfo(slots[i], &info) == CKR_OK &&
		    label_is(info.label, sizeof(info.label), label)) {
			*out = slots[i];
			return 0;
		}

	if (!allow_blank)
		return -1;

	for (CK_ULONG i = 0; i < count; i++)
		if (fn->C_GetTokenInfo(slots[i], &info) == CKR_OK &&
		    !(info.flags & CKF_TOKEN_INITIALIZED)) {
			*out = slots[i];
			return 0;
		}

	return -1;
}

static int session_for(const char *label, const char *pin,
		       CK_SESSION_HANDLE *session)
{
	CK_SLOT_ID slot;

	if (find_token(label, 0, &slot)) {
		fprintf(stderr, "no token labelled \"%s\"; run init first\n", label);
		return 1;
	}

	CHECK(fn->C_OpenSession(slot, CKF_SERIAL_SESSION | CKF_RW_SESSION, NULL,
				NULL, session), "C_OpenSession");
	CHECK(fn->C_Login(*session, CKU_USER, (CK_UTF8CHAR_PTR)pin, strlen(pin)),
	      "C_Login");
	return 0;
}

static int find_object_quiet(CK_SESSION_HANDLE session, CK_OBJECT_CLASS class,
			     const char *label, CK_OBJECT_HANDLE *out)
{
	CK_ULONG found = 0;
	CK_RV rv, rvf;
	CK_ATTRIBUTE search[] = {
		{ CKA_CLASS, &class, sizeof(class) },
		{ CKA_LABEL, (void *)label, strlen(label) },
	};

	CHECK(fn->C_FindObjectsInit(session, search, 2), "C_FindObjectsInit");

	/*
	 * A search left open makes the next C_FindObjectsInit on this session
	 * answer CKR_OPERATION_ACTIVE, which in serve wedges every later
	 * lookup. Finalise on the way out whether or not the search worked.
	 */
	rv = fn->C_FindObjects(session, out, 1, &found);
	rvf = fn->C_FindObjectsFinal(session);

	CHECK(rv, "C_FindObjects");
	CHECK(rvf, "C_FindObjectsFinal");

	return found ? 0 : 1;
}

static int find_object(CK_SESSION_HANDLE session, CK_OBJECT_CLASS class,
		       const char *label, CK_OBJECT_HANDLE *out)
{
	if (find_object_quiet(session, class, label, out)) {
		fprintf(stderr, "no object labelled \"%s\"\n", label);
		return 1;
	}

	return 0;
}

static int cmd_info(void)
{
	CK_SLOT_ID slots[MAX_SLOTS];
	CK_ULONG count = MAX_SLOTS;
	CK_TOKEN_INFO info;

	CHECK(fn->C_GetSlotList(CK_TRUE, slots, &count), "C_GetSlotList");

	for (CK_ULONG i = 0; i < count; i++) {
		if (fn->C_GetTokenInfo(slots[i], &info) != CKR_OK)
			continue;

		printf("slot=%lu initialised=%d user_pin_set=%d label=\"%.*s\"\n",
		       (unsigned long)slots[i],
		       !!(info.flags & CKF_TOKEN_INITIALIZED),
		       !!(info.flags & CKF_USER_PIN_INITIALIZED),
		       (int)sizeof(info.label), info.label);
	}

	return 0;
}

static int cmd_init(const char *label, const char *so_pin, const char *pin,
		    int reinit)
{
	CK_SLOT_ID slot;
	CK_SESSION_HANDLE session = 0;
	CK_TOKEN_INFO info;
	CK_UTF8CHAR padded[32];

	/*
	 * C_InitToken destroys every object the token holds. On a provisioned
	 * device that is the device key and its certificate, so an accidental
	 * re-run must not be able to do it.
	 */
	if (strlen(label) > 32) {
		fprintf(stderr, "label is longer than the 32 bytes a token holds\n");
		return 1;
	}

	if (find_token(label, 1, &slot)) {
		fprintf(stderr, "no free token to initialise\n");
		return 1;
	}

	if (fn->C_GetTokenInfo(slot, &info) == CKR_OK &&
	    (info.flags & CKF_TOKEN_INITIALIZED) && !reinit) {
		fprintf(stderr,
			"token \"%s\" is already initialised; refusing.\n"
			"Initialising destroys every key it holds. Pass reinit "
			"instead if that is what you want.\n", label);
		return 1;
	}

	/* C_InitToken wants the label space-padded to exactly 32 bytes */
	memset(padded, ' ', sizeof(padded));
	memcpy(padded, label, strlen(label) > 32 ? 32 : strlen(label));

	CHECK(fn->C_InitToken(slot, (CK_UTF8CHAR_PTR)so_pin, strlen(so_pin),
			      padded), "C_InitToken");
	CHECK(fn->C_OpenSession(slot, CKF_SERIAL_SESSION | CKF_RW_SESSION, NULL,
				NULL, &session), "C_OpenSession");
	CHECK(fn->C_Login(session, CKU_SO, (CK_UTF8CHAR_PTR)so_pin,
			  strlen(so_pin)), "C_Login(SO)");
	CHECK(fn->C_InitPIN(session, (CK_UTF8CHAR_PTR)pin, strlen(pin)),
	      "C_InitPIN");
	CHECK(fn->C_Logout(session), "C_Logout");
	CHECK(fn->C_CloseSession(session), "C_CloseSession");

	printf("slot=%lu label=\"%s\"\n", (unsigned long)slot, label);
	return 0;
}

static int cmd_generate(const char *label, const char *pin, const char *key)
{
	CK_SESSION_HANDLE session = 0;
	CK_OBJECT_HANDLE pub = 0, priv = 0;
	CK_OBJECT_HANDLE existing = 0;
	CK_MECHANISM mech = { CKM_EC_KEY_PAIR_GEN, NULL, 0 };
	CK_BBOOL yes = CK_TRUE, no = CK_FALSE;
	/*
	 * The two halves are found by label, so a second pair under the same
	 * label makes "the public key" and "the private key" ambiguous - and
	 * a certificate issued for one would be verified against the other.
	 * A shared CKA_ID ties this pair together; refusing a duplicate keeps
	 * the label unambiguous in the first place.
	 */
	CK_BYTE id[16];
	int rc;

	CK_ATTRIBUTE pub_tmpl[] = {
		{ CKA_EC_PARAMS, (void *)ec_params, sizeof(ec_params) },
		{ CKA_TOKEN, &yes, sizeof(yes) },
		{ CKA_VERIFY, &yes, sizeof(yes) },
		{ CKA_LABEL, (void *)key, strlen(key) },
		{ CKA_ID, id, sizeof(id) },
	};
	CK_ATTRIBUTE priv_tmpl[] = {
		{ CKA_TOKEN, &yes, sizeof(yes) },
		{ CKA_SIGN, &yes, sizeof(yes) },
		{ CKA_PRIVATE, &yes, sizeof(yes) },
		{ CKA_SENSITIVE, &yes, sizeof(yes) },
		/* the point of all this: no path back out of the TA */
		{ CKA_EXTRACTABLE, &no, sizeof(no) },
		{ CKA_LABEL, (void *)key, strlen(key) },
		{ CKA_ID, id, sizeof(id) },
	};

	rc = session_for(label, pin, &session);
	if (rc)
		return rc;

	if (!find_object_quiet(session, CKO_PRIVATE_KEY, key, &existing)) {
		fprintf(stderr,
			"a private key labelled \"%s\" already exists.\n"
			"Generating another would leave two under one label and "
			"no way to tell which a certificate belongs to.\n", key);
		return 1;
	}

	CHECK(fn->C_GenerateRandom(session, id, sizeof(id)), "C_GenerateRandom");

	CHECK(fn->C_GenerateKeyPair(session, &mech, pub_tmpl, 5, priv_tmpl, 7,
				    &pub, &priv), "C_GenerateKeyPair");
	CHECK(fn->C_Logout(session), "C_Logout");
	CHECK(fn->C_CloseSession(session), "C_CloseSession");

	printf("label=\"%s\"\n", key);
	return 0;
}

/* hex of the SubjectPublicKeyInfo for the token's public key */
static int pubkey_hex(CK_SESSION_HANDLE session, const char *key,
		      char *out, size_t max)
{
	CK_OBJECT_HANDLE obj = 0;
	CK_BYTE point[256];
	CK_ATTRIBUTE value = { CKA_EC_POINT, point, sizeof(point) };
	unsigned char spki[sizeof(spki_prefix) + 65];
	const CK_BYTE *raw;
	size_t raw_len, header;

	if (find_object(session, CKO_PUBLIC_KEY, key, &obj))
		return 1;

	CHECK(fn->C_GetAttributeValue(session, obj, &value, 1),
	      "C_GetAttributeValue");

	/*
	 * CKA_EC_POINT is the point inside a DER OCTET STRING. The length
	 * bytes come from the token, so check them against how much it
	 * actually returned before reading anything they describe.
	 */
	if (value.ulValueLen < 2 || point[0] != 0x04) {
		fprintf(stderr, "unexpected CKA_EC_POINT encoding\n");
		return 1;
	}

	if (point[1] & 0x80) {
		if ((point[1] & 0x7f) != 1 || value.ulValueLen < 3) {
			fprintf(stderr, "unexpected CKA_EC_POINT length\n");
			return 1;
		}
		raw_len = point[2];
		raw = &point[3];
		header = 3;
	} else {
		raw_len = point[1];
		raw = &point[2];
		header = 2;
	}

	if (raw_len > value.ulValueLen - header) {
		fprintf(stderr, "CKA_EC_POINT claims %zu bytes, token returned %lu\n",
			raw_len, (unsigned long)value.ulValueLen);
		return 1;
	}

	if (raw_len != 65 || raw[0] != 0x04) {
		fprintf(stderr, "expected a 65-byte uncompressed point\n");
		return 1;
	}

	memcpy(spki, spki_prefix, sizeof(spki_prefix));
	memcpy(spki + sizeof(spki_prefix), raw, raw_len);

	return to_hex(spki, sizeof(spki), out, max);
}

/*
 * Sign a digest the caller has already computed. CKM_ECDSA takes the digest,
 * not the message, and returns r||s; X.509 wants them in a SEQUENCE, so wrap
 * it here rather than making every caller do it.
 */
static int sign_hex(CK_SESSION_HANDLE session, const char *key,
		    const char *digest_hex, char *out, size_t max)
{
	CK_OBJECT_HANDLE obj = 0;
	CK_MECHANISM mech = { CKM_ECDSA, NULL, 0 };
	unsigned char digest[64];
	unsigned char sig[128];
	unsigned char der[160];
	size_t digest_len;
	CK_ULONG sig_len = sizeof(sig);
	size_t half, r_len, s_len, i = 0;
	const unsigned char *r, *s;

	if (get_hex(digest_hex, digest, sizeof(digest), &digest_len)) {
		fprintf(stderr, "bad hex digest\n");
		return 1;
	}

	if (find_object(session, CKO_PRIVATE_KEY, key, &obj))
		return 1;

	CHECK(fn->C_SignInit(session, &mech, obj), "C_SignInit");
	CHECK(fn->C_Sign(session, digest, digest_len, sig, &sig_len), "C_Sign");

	if (sig_len != 64) {
		fprintf(stderr, "expected a 64-byte P-256 signature, got %lu\n",
			(unsigned long)sig_len);
		return 1;
	}

	half = sig_len / 2;

	/* DER integers carry no leading zeros, and need one if the top bit is set */
	r = sig;
	r_len = half;
	while (r_len > 1 && r[0] == 0) { r++; r_len--; }

	s = sig + half;
	s_len = half;
	while (s_len > 1 && s[0] == 0) { s++; s_len--; }

	der[i++] = 0x30;
	der[i++] = 0; /* length, filled in below */

	der[i++] = 0x02;
	der[i++] = (unsigned char)(r_len + ((r[0] & 0x80) ? 1 : 0));
	if (r[0] & 0x80)
		der[i++] = 0x00;
	memcpy(der + i, r, r_len);
	i += r_len;

	der[i++] = 0x02;
	der[i++] = (unsigned char)(s_len + ((s[0] & 0x80) ? 1 : 0));
	if (s[0] & 0x80)
		der[i++] = 0x00;
	memcpy(der + i, s, s_len);
	i += s_len;

	der[1] = (unsigned char)(i - 2);

	return to_hex(der, i, out, max);
}

static int cmd_pubkey(const char *label, const char *pin, const char *key)
{
	CK_SESSION_HANDLE session = 0;
	char hex[512];
	int rc;

	rc = session_for(label, pin, &session);
	if (rc)
		return rc;

	rc = pubkey_hex(session, key, hex, sizeof(hex));
	if (!rc)
		printf("%s\n", hex);

	(void)fn->C_Logout(session);
	(void)fn->C_CloseSession(session);
	return rc;
}

static int cmd_sign(const char *label, const char *pin, const char *key,
		    const char *digest_hex)
{
	CK_SESSION_HANDLE session = 0;
	char hex[512];
	int rc;

	rc = session_for(label, pin, &session);
	if (rc)
		return rc;

	rc = sign_hex(session, key, digest_hex, hex, sizeof(hex));
	if (!rc)
		printf("%s\n", hex);

	(void)fn->C_Logout(session);
	(void)fn->C_CloseSession(session);
	return rc;
}

static int serve_pubkey(CK_SESSION_HANDLE session, const char *key)
{
	char hex[512];

	if (pubkey_hex(session, key, hex, sizeof(hex)))
		return 1;

	printf("ok %s\n", hex);
	return 0;
}

static int serve_sign(CK_SESSION_HANDLE session, const char *key,
		      const char *digest_hex)
{
	char hex[512];

	if (sign_hex(session, key, digest_hex, hex, sizeof(hex)))
		return 1;

	printf("ok %s\n", hex);
	return 0;
}

/*
 * Answer commands until stdin closes, holding one session open throughout.
 * A failed command is reported on its answer line rather than by exiting, so
 * one bad request does not take the session down with it.
 */
static int cmd_serve(const char *label, const char *pin, const char *key)
{
	CK_SESSION_HANDLE session = 0;
	char line[512];
	int rc;

	rc = session_for(label, pin, &session);
	if (rc)
		return rc;

	setvbuf(stdout, NULL, _IOLBF, 0);

	while (fgets(line, sizeof(line), stdin)) {
		line[strcspn(line, "\r\n")] = 0;

		if (!strncmp(line, "sign ", 5)) {
			if (serve_sign(session, key, line + 5))
				printf("error sign failed\n");
		} else if (!strcmp(line, "pubkey")) {
			if (serve_pubkey(session, key))
				printf("error pubkey failed\n");
		} else if (!strcmp(line, "quit")) {
			break;
		} else {
			printf("error unknown command\n");
		}
	}

	(void)fn->C_Logout(session);
	(void)fn->C_CloseSession(session);
	return 0;
}

static void usage(const char *me)
{
	fprintf(stderr,
		"usage: %s info\n"
		"       %s init     <token>\n"
		"       %s reinit   <token>              # destroys every key\n"
		"       %s generate <token> <key-label>\n"
		"       %s pubkey   <token> <key-label>\n"
		"       %s sign     <token> <key-label> <hex-digest>\n"
		"       %s serve    <token> <key-label>\n"
		"\n"
		"PINs come from the environment, never the command line:\n"
		"  OPTEE_KEY_PIN     the user PIN, for every command but info\n"
		"  OPTEE_KEY_SO_PIN  the security officer PIN, for init and reinit\n",
		me, me, me, me, me, me, me);
}

/*
 * An empty PIN is not a PIN.  getenv() returning "" would otherwise reach
 * C_Login as a zero-length credential, which some tokens accept.
 */
static const char *pin_from_env(const char *name)
{
	const char *v = getenv(name);

	if (!v || !*v) {
		fprintf(stderr, "%s is not set\n", name);
		return NULL;
	}

	return v;
}

int main(int argc, char **argv)
{
	const char *pin = NULL;
	const char *so_pin = NULL;
	int rc;

	if (argc < 2) {
		usage(argv[0]);
		return 2;
	}

	/* info is the one command that opens no session. */
	if (strcmp(argv[1], "info")) {
		pin = pin_from_env("OPTEE_KEY_PIN");
		if (!pin)
			return 2;
	}

	if (!strcmp(argv[1], "init") || !strcmp(argv[1], "reinit")) {
		so_pin = pin_from_env("OPTEE_KEY_SO_PIN");
		if (!so_pin)
			return 2;
	}

	CHECK(C_GetFunctionList(&fn), "C_GetFunctionList");
	CHECK(fn->C_Initialize(NULL), "C_Initialize");

	if (!strcmp(argv[1], "info") && argc == 2)
		rc = cmd_info();
	else if (!strcmp(argv[1], "init") && argc == 3)
		rc = cmd_init(argv[2], so_pin, pin, 0);
	else if (!strcmp(argv[1], "reinit") && argc == 3)
		rc = cmd_init(argv[2], so_pin, pin, 1);
	else if (!strcmp(argv[1], "generate") && argc == 4)
		rc = cmd_generate(argv[2], pin, argv[3]);
	else if (!strcmp(argv[1], "pubkey") && argc == 4)
		rc = cmd_pubkey(argv[2], pin, argv[3]);
	else if (!strcmp(argv[1], "sign") && argc == 5)
		rc = cmd_sign(argv[2], pin, argv[3], argv[4]);
	else if (!strcmp(argv[1], "serve") && argc == 4)
		rc = cmd_serve(argv[2], pin, argv[3]);
	else {
		usage(argv[0]);
		rc = 2;
	}

	(void)fn->C_Finalize(NULL);
	return rc;
}
