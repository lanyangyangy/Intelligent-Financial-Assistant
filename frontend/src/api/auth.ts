import { http } from './http'
export interface TokenResponse { access_token:string; refresh_token:string; token_type:string; expires_in:number }
export interface RegisterResponse { id:string; username:string; display_name:string; account_type:'customer'|'employee'; status:string; role:string }
export interface MeResponse { id:string; username:string; display_name:string; roles:string[]; permissions:string[]; is_super_admin:boolean }
export const authApi = { login:(username:string,password:string)=>http.post<{data:TokenResponse}>('/auth/login',{username,password}), register:(payload:{register_type:'customer'|'employee';username:string;password:string;display_name:string;company_code?:string})=>http.post<{data:RegisterResponse}>('/auth/register',payload), refresh:(refresh_token:string)=>http.post<{data:TokenResponse}>('/auth/refresh',{refresh_token}), logout:(refresh_token:string)=>http.post('/auth/logout',{refresh_token}), me:()=>http.get<{data:MeResponse}>('/auth/me') }
