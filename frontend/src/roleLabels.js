export function teammateRoleLabel(roleLabel) {
  const value = String(roleLabel || '').trim()
  if (!value) return 'teammate'
  if (/^on[-\s]?call(\s+engineer)?$/i.test(value)) return 'teammate'
  return value
}
