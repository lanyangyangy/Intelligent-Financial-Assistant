export const CUSTOMER_ROLE_CODES = ['retail_investor', 'high_net_worth_customer'] as const

export const ROLE_LABELS: Record<string, string> = {
  retail_investor: '零售投资者',
  high_net_worth_customer: '高净值客户',
  financial_advisor: '理财顾问',
  risk_specialist: '风控专员',
  customer_manager: '客户经理',
  auditor: '审计',
  employee_pending: '待分配员工',
  super_admin: '系统管理员',
}

export function isCustomerRoles(roles: string[] | undefined | null): boolean {
  return Boolean(roles?.some(role => CUSTOMER_ROLE_CODES.includes(role as typeof CUSTOMER_ROLE_CODES[number])))
}

export function roleLabel(role: string | undefined | null): string {
  return role ? (ROLE_LABELS[role] || role) : '未分配角色'
}

