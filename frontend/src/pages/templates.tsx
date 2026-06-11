import { useNavigate } from 'react-router-dom'
import { DashboardLayout } from '@/layouts/dashboard-layout'
import { TemplateGallery } from '@/features/templates/components/TemplateGallery'

export default function TemplatesPage() {
  const navigate = useNavigate()
  return (
    <DashboardLayout>
      <TemplateGallery
        onTemplateCloned={(agentId) => navigate(`/agents/${agentId}/canvas`)}
      />
    </DashboardLayout>
  )
}
