import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import HomeView from './views/HomeView.vue'
import LoginView from './views/LoginView.vue'
import RegisterView from './views/RegisterView.vue'
import PublicProductsView from './views/PublicProductsView.vue'
import PlaceholderView from './views/PlaceholderView.vue'
import OrdersView from './views/OrdersView.vue'
import StaffOrdersView from './views/StaffOrdersView.vue'
import StaffCustomersView from './views/StaffCustomersView.vue'
import RiskAssessmentView from './views/RiskAssessmentView.vue'
import CustomerBackofficeView from './views/CustomerBackofficeView.vue'
import EmployeeWorkspaceView from './views/EmployeeWorkspaceView.vue'
import AdminWorkspaceView from './views/AdminWorkspaceView.vue'
import BackofficeShellView from './views/BackofficeShellView.vue'
import AgentChatView from './views/AgentChatView.vue'
import { isCustomerRoles } from './constants/roles'
import { useAuth } from './stores/auth'
import './styles.css'
const placeholder=(path:string,title:string)=>({path,component:PlaceholderView,meta:{requiresAuth:true,title}})
const router=createRouter({history:createWebHistory(),routes:[{path:'/login',component:LoginView},{path:'/register',component:RegisterView},{path:'/',component:HomeView},{path:'/products',component:PublicProductsView},{path:'/chat',component:AgentChatView,meta:{requiresAuth:true,title:'智能助手'}},{path:'/customer-center',component:CustomerBackofficeView,meta:{requiresAuth:true,title:'客户个人中心'}},{path:'/backoffice',component:BackofficeShellView,meta:{requiresAuth:true,title:'管理后台'}},{path:'/admin-workspace',redirect:'/backoffice'},{path:'/employee-workspace',redirect:'/backoffice'},{path:'/customer-center/orders',redirect:'/customer-center'},{path:'/customer-center/risk-assessment',component:RiskAssessmentView,meta:{requiresAuth:true,title:'风险测评'}},{path:'/employee-workspace/orders',component:StaffOrdersView,meta:{requiresAuth:true,title:'订单审核与执行'}},{path:'/employee-workspace/customers',component:StaffCustomersView,meta:{requiresAuth:true,title:'客户管理'}}]})
const auth = useAuth()
router.beforeEach(async (to)=>{
  const loggedIn=!!sessionStorage.getItem('access_token')
  if(to.meta.requiresAuth&&!loggedIn)return'/login'
  if((to.path==='/login'||to.path==='/register')&&loggedIn)return'/'
  if(loggedIn && !auth.me.value) await auth.loadMe()
  if (isCustomerRoles(auth.me.value?.roles) && to.path.startsWith('/backoffice')) return '/customer-center'
  if (auth.me.value && !isCustomerRoles(auth.me.value.roles) && to.path.startsWith('/customer-center')) return '/backoffice'
})
createApp(App).use(router).mount('#app')
