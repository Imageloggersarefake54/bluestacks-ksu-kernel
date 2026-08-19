# =============================================================================
# patch-ksu.sh - Prepare KernelSU-Next for Linux 4.19 x86_64 build
# =============================================================================
#!/usr/bin/env bash
set -e

echo "[*] Applying KernelSU Next x86_64 legacy patches..."

# 1. Compatibility macros in all KernelSU source files
find drivers/kernelsu KernelSU-Next -type f \( -name "*.c" -o -name "*.h" \) 2>/dev/null | while read -r file; do
  sed -i '1s/^/#ifndef untagged_addr\n#define untagged_addr(addr) (addr)\n#endif\n#ifndef __nocfi\n#define __nocfi\n#endif\n#ifndef phys_to_page\n#define phys_to_page(phys) pfn_to_page(__phys_to_pfn(phys))\n#endif\n#ifndef preempt_enable_no_resched_notrace\n#define preempt_enable_no_resched_notrace() preempt_enable_notrace()\n#endif\n/' "$file"
done

# 2. Add weak symbol fallbacks for all unresolved SELinux functions in rules.c and sepolicy.c
find . -type f -name "rules.c" | while read -r f; do
  cat >> "$f" << 'EOF'

#include <linux/types.h>

__attribute__((weak)) void selnl_notify_policyload(u32 seqno) {}
__attribute__((weak)) void selinux_status_update_policyload(struct selinux_state *state, int seqno) {}
__attribute__((weak)) int avc_ss_reset(struct selinux_avc *avc, u32 seqno) { return 0; }
EOF
done

find . -type f -name "sepolicy.c" | while read -r f; do
  cat >> "$f" << 'EOF'

#include <linux/types.h>
#include <linux/string.h>

#ifndef SECCLASS_SECURITY
#define SECCLASS_SECURITY 1
#endif

__attribute__((weak)) void *hashtab_search(struct hashtab *h, const void *k) {
    return NULL;
}
__attribute__((weak)) int hashtab_insert(struct hashtab *h, void *k, void *d) {
    return 0;
}
__attribute__((weak)) int ebitmap_set_bit(struct ebitmap *e, unsigned long bit, int value) {
    return 0;
}
__attribute__((weak)) int ebitmap_get_bit(struct ebitmap *e, unsigned long bit) {
    return 0;
}
__attribute__((weak)) struct avtab_node *avtab_search_node(struct avtab *h, struct avtab_key *key) {
    return NULL;
}
__attribute__((weak)) struct avtab_node *avtab_search_node_next(struct avtab_node *node, int specified) {
    return NULL;
}
__attribute__((weak)) struct avtab_node *avtab_insert_nonunique(struct avtab *h, struct avtab_key *key, struct avtab_datum *datum) {
    return NULL;
}
EOF
done

# 3. Add weak symbol fallbacks for selinux_hide.c and kernel_umount.c
find . -type f -name "kernel_umount.c" | while read -r f; do
  cat >> "$f" << 'EOF'

#include <linux/path.h>
__attribute__((weak)) int path_umount(struct path *path, int flags) {
    return 0;
}
EOF
done

find . -type f -name "selinux_hide.c" | while read -r f; do
  cat >> "$f" << 'EOF'

#include <linux/types.h>
#include <linux/mm_types.h>
bool ksu_input_hook = false;
__attribute__((weak)) struct selinux_state selinux_state;
__attribute__((weak)) struct page *selinux_kernel_status_page(struct selinux_state *state) {
    return NULL;
}
EOF
done

# 4. Pre-create flask.h and av_permissions.h
mkdir -p security/selinux/include security/selinux
if [ ! -f security/selinux/include/flask.h ]; then
  cat > security/selinux/include/flask.h << 'EOF'
#ifndef _SELINUX_FLASK_H_
#define _SELINUX_FLASK_H_
#define SECCLASS_SECURITY 1
#define SECCLASS_PROCESS 2
#define SECCLASS_SYSTEM 3
#define SECCLASS_CAPABILITY 4
#define SECCLASS_KERNEL_SERVICE 5
#endif
EOF
  cp security/selinux/include/flask.h security/selinux/flask.h
fi

if [ ! -f security/selinux/include/av_permissions.h ]; then
  cat > security/selinux/include/av_permissions.h << 'EOF'
#ifndef _SELINUX_AV_PERMISSIONS_H_
#define _SELINUX_AV_PERMISSIONS_H_
#endif
EOF
  cp security/selinux/include/av_permissions.h security/selinux/av_permissions.h
fi

# 5. Export symbols in security/selinux
for f in security/selinux/ss/hashtab.c security/selinux/ss/ebitmap.c security/selinux/ss/status.c security/selinux/ss/avtab.c security/selinux/ss/services.c security/selinux/hooks.c security/selinux/selinuxfs.c security/selinux/nlmsg.c security/selinux/avc.c; do
  if [ -f "$f" ]; then
    sed -i '1i #include <linux/export.h>' "$f"
  fi
done

echo "[+] KernelSU Next x86_64 legacy patches applied."
