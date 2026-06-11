/**
 * KPI Goals & Improvement Plans Page
 *
 * Implements KPI goal planning with AI-powered recommendations.
 * Allows users to set targets and get step-by-step improvement plans.
 */

import { useState, useEffect } from 'react';
import { useParams, useSearchParams, useNavigate } from 'react-router-dom';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/atoms/tabs';
import { Target, TrendingUp, CheckCircle } from 'lucide-react';
import { DashboardLayout } from '@/layouts/dashboard-layout';
import {
  KPIGoalList,
  KPIGoalForm,
  KPIGoalDashboard,
  KPIGoalDetail,
} from '@/features/kpi-goals/components';

export default function KPIGoalsPage() {
  const { goalId } = useParams<{ goalId?: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const tabParam = searchParams.get('tab');
  const [selectedTab, setSelectedTab] = useState(tabParam || 'dashboard');
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    if (tabParam) {
      setSelectedTab(tabParam);
    }
  }, [tabParam]);

  const handleGoalCreated = () => {
    setRefreshKey(prev => prev + 1);
    setSelectedTab('goals');
    navigate('/kpi-goals?tab=goals');
  };

  const handleBackFromDetail = () => {
    navigate('/kpi-goals?tab=goals');
  };

  // If goalId is present, show detail view
  if (goalId) {
    return (
      <DashboardLayout>
        <KPIGoalDetail goalId={goalId} onBack={handleBackFromDetail} />
      </DashboardLayout>
    );
  }

  return (
    <DashboardLayout>
      <div className="space-y-6">
        <Tabs value={selectedTab} onValueChange={setSelectedTab}>
          <TabsList className="grid w-full max-w-md grid-cols-3">
            <TabsTrigger value="dashboard" className="gap-2">
              <TrendingUp className="h-4 w-4" />
              Dashboard
            </TabsTrigger>
            <TabsTrigger value="goals" className="gap-2">
              <Target className="h-4 w-4" />
              Goals
            </TabsTrigger>
            <TabsTrigger value="create" className="gap-2">
              <CheckCircle className="h-4 w-4" />
              Create Goal
            </TabsTrigger>
          </TabsList>

          <TabsContent value="dashboard" className="space-y-4">
            <KPIGoalDashboard key={refreshKey} />
          </TabsContent>

          <TabsContent value="goals" className="space-y-4">
            <KPIGoalList key={refreshKey} />
          </TabsContent>

          <TabsContent value="create" className="space-y-4">
            <KPIGoalForm onSuccess={handleGoalCreated} />
          </TabsContent>
        </Tabs>
      </div>
    </DashboardLayout>
  );
}
