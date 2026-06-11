/**
 * Settings Page
 *
 * Manage user profile, organization, team members, and billing.
 * Manage user profile, organization, team members, and billing.
 */

import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { DashboardLayout } from '@/layouts/dashboard-layout'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/atoms/card'
import { Button } from '@/components/atoms/button'
import { Input } from '@/components/atoms/input'
import { Label } from '@/components/atoms/label'
import { Badge } from '@/components/atoms/badge'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/atoms/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/atoms/tabs'
import { Avatar, AvatarWithName } from '@/components/atoms/avatar'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/atoms/dialog'
import { useAuthStore } from '@/store/auth.store'
import {
  User,
  Building2,
  Users,
  CreditCard,
  Palette,
  Save,
  Copy,
  Plus,
  Trash2,
  Key,
  Shield,
  Monitor,
  RefreshCw,
  Loader2,
  AlertTriangle,
  RotateCcw,
} from 'lucide-react'
import { formatDate } from '@/lib/utils'
import { settingsService } from '@/api/services/settings.service'
import type { Organization, OrganizationMember, OrganizationRole, APIKeyPublic, ClosureStatus } from '@/types'
import { toast } from 'sonner'
import { TimezonePicker } from './components/TimezonePicker'
import { AppearanceSettings } from './components/AppearanceSettings'

export default function SettingsPage() {
  const { user, setUser } = useAuthStore()
  const { tab } = useParams()
  const navigate = useNavigate()
  const [isInviteDialogOpen, setIsInviteDialogOpen] = useState(false)
  const [isApiKeyDialogOpen, setIsApiKeyDialogOpen] = useState(false)
  const [savingProfile, setSavingProfile] = useState(false)
  const [savingOrg, setSavingOrg] = useState(false)
  const [invitingUser, setInvitingUser] = useState(false)
  const [removingUser, setRemovingUser] = useState(false)
  const [isAvatarUploading, setIsAvatarUploading] = useState(false)
  const [isInitialLoading, setIsInitialLoading] = useState(true)
  const [teamMembers, setTeamMembers] = useState<OrganizationMember[]>([])
  const [organization, setOrganization] = useState<Organization | null>(null)
  const [apiKeys, setApiKeys] = useState<APIKeyPublic[]>([])
  const [newKeyPlaintext, setNewKeyPlaintext] = useState<string | null>(null)
  const [apiKeyName, setApiKeyName] = useState('')
  const [creatingKey, setCreatingKey] = useState(false)
  const [revokingKeyId, setRevokingKeyId] = useState<string | null>(null)

  // Danger zone — account closure
  const [closureStep, setClosureStep] = useState<'idle' | 'confirm' | 'sent_close' | 'reactivate' | 'sent_reactivate'>('idle')
  const [closureForm, setClosureForm] = useState({ confirm_org_name: '', reason: '' })
  const [closureLoading, setClosureLoading] = useState(false)
  const [closureStatus, setClosureStatus] = useState<ClosureStatus | null>(null)

  // Form state
  const [profileForm, setProfileForm] = useState({
    display_name: user?.display_name || '',
    timezone: user?.timezone || 'UTC'
  })

  const [orgForm, setOrgForm] = useState({
    name: '',
  })

  const [inviteForm, setInviteForm] = useState({
    email: '',
    role: 'analyst'
  })

  // Load organization and team data
  useEffect(() => {
    const loadData = async () => {
      if (!user?.organization?.organization_id) {
        setIsInitialLoading(false)
        return
      }

      try {
        setIsInitialLoading(true)
        const [orgData, teamData, keysData] = await Promise.all([
          settingsService.getOrganization(user.organization.organization_id),
          settingsService.listUsers(),
          settingsService.listApiKeys().catch(() => []),
        ])
        setOrganization(orgData)
        setTeamMembers(teamData)
        setApiKeys(keysData)
        setOrgForm(prev => ({ ...prev, name: orgData.name }))
      } catch (error) {
        toast.error('Failed to load settings data. Please try refreshing the page.')
      } finally {
        setIsInitialLoading(false)
      }
    }

    loadData()
  }, [user?.organization?.organization_id])

  // Determine active tab from URL parameter, default to 'profile'
  const activeTab = tab || 'profile'

  // Handle tab changes by updating the URL
  const handleTabChange = (value: string) => {
    navigate(`/settings/${value}`)
  }

  const handleSaveProfile = async () => {
    if (!user?.user_id) return

    try {
      setSavingProfile(true)
      const updatedUser = await settingsService.updateUser(user.user_id, {
        display_name: profileForm.display_name,
        timezone: profileForm.timezone
      })
      setUser(updatedUser)
      toast.success('Profile updated successfully!')
    } catch (error: any) {
      toast.error(`Failed to update profile: ${error.message}`)
    } finally {
      setSavingProfile(false)
    }
  }

  const handleSaveOrganization = async () => {
    if (!user?.organization?.organization_id) return

    try {
      setSavingOrg(true)
      const updatedOrg = await settingsService.updateOrganization(user.organization.organization_id, {
        name: orgForm.name
      })
      setOrganization(updatedOrg)

      // Sync the updated org name back to the auth store so the sidebar
      // (and any other component reading useAuthStore) reflects the change.
      setUser({
        ...user,
        organization: {
          ...user.organization,
          name: updatedOrg.name,
          icon_url: updatedOrg.icon_url ?? user.organization?.icon_url,
        },
      })

      toast.success('Organization updated successfully!')
    } catch (error: any) {
      toast.error(`Failed to update organization: ${error.message}`)
    } finally {
      setSavingOrg(false)
    }
  }

  const handleInviteUser = async () => {
    if (!inviteForm.email) {
      toast.warning('Please enter an email address')
      return
    }

    try {
      setInvitingUser(true)
      await settingsService.createInvitation({
        email: inviteForm.email,
        role: inviteForm.role as OrganizationRole
      })
      toast.success(`Invitation sent to ${inviteForm.email}!`)
      setInviteForm({ email: '', role: 'analyst' })
      setIsInviteDialogOpen(false)
    } catch (error: any) {
      toast.error(`Failed to send invitation: ${error.message}`)
    } finally {
      setInvitingUser(false)
    }
  }

  const handleCreateApiKey = async () => {
    if (!apiKeyName.trim()) {
      toast.warning('Please enter a name for the API key')
      return
    }

    try {
      setCreatingKey(true)
      const created = await settingsService.createApiKey(apiKeyName.trim())
      setApiKeys(prev => [created, ...prev])
      setNewKeyPlaintext(created.key)
      setApiKeyName('')
      setIsApiKeyDialogOpen(false)
      toast.success('API key created! Copy it now — it won\'t be shown again.')
    } catch (error: any) {
      toast.error(`Failed to create API key: ${error.message}`)
    } finally {
      setCreatingKey(false)
    }
  }

  const handleRevokeApiKey = async (id: string) => {
    if (!window.confirm('Are you sure you want to revoke this API key? This cannot be undone.')) {
      return
    }

    try {
      setRevokingKeyId(id)
      await settingsService.revokeApiKey(id)
      setApiKeys(prev => prev.filter(k => k.key_id !== id))
      if (newKeyPlaintext) setNewKeyPlaintext(null)
      toast.success('API key revoked successfully')
    } catch (error: any) {
      toast.error(`Failed to revoke API key: ${error.message}`)
    } finally {
      setRevokingKeyId(null)
    }
  }

  const handleRemoveUser = async (id: string, name: string) => {
    if (!window.confirm(`Are you sure you want to remove ${name} from the organization?`)) {
      return
    }

    try {
      setRemovingUser(true)
      await settingsService.deleteUser(id)
      setTeamMembers(prev => prev.filter(member => member.user_id !== id))
      toast.success('User removed successfully!')
    } catch (error: any) {
      toast.error(`Failed to remove user: ${error.message}`)
    } finally {
      setRemovingUser(false)
    }
  }

  const handleCloseAccount = async () => {
    try {
      setClosureLoading(true)
      await settingsService.closeAccount(closureForm.confirm_org_name, closureForm.reason || undefined)
      setClosureForm({ confirm_org_name: '', reason: '' })
      setClosureStep('sent_close')
    } catch (error: any) {
      toast.error(error.message || 'Failed to initiate account closure.')
    } finally {
      setClosureLoading(false)
    }
  }

  const handleReactivateAccount = async () => {
    if (!user?.organization?.organization_id) return
    try {
      setClosureLoading(true)
      await settingsService.reactivateAccount()
      setClosureStep('sent_reactivate')
    } catch (error: any) {
      toast.error(error.message || 'Failed to initiate reactivation.')
    } finally {
      setClosureLoading(false)
    }
  }

  if (isInitialLoading) {
    return (
      <DashboardLayout>
        <div className="flex min-h-[40vh] items-center justify-center">
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading settings...
          </div>
        </div>
      </DashboardLayout>
    )
  }

  return (
    <DashboardLayout>
      <div className="space-y-6">
        {/* Page Header */}
        <div>
          <h1 className="text-3xl font-bold">Settings</h1>
          <p className="mt-1 text-muted-foreground">
            Manage your account, organization, and preferences
          </p>
        </div>

        {/* Settings Tabs */}
        <Tabs value={activeTab} onValueChange={handleTabChange} className="space-y-4">
          <TabsList className="grid w-full grid-cols-5 lg:w-[750px]">
            <TabsTrigger value="profile">
              <User className="mr-2 h-4 w-4" />
              Profile
            </TabsTrigger>
            <TabsTrigger value="organization">
              <Building2 className="mr-2 h-4 w-4" />
              Organization
            </TabsTrigger>
            <TabsTrigger value="team">
              <Users className="mr-2 h-4 w-4" />
              Team
            </TabsTrigger>
            <TabsTrigger value="appearance">
              <Palette className="mr-2 h-4 w-4" />
              Appearance
            </TabsTrigger>
            <TabsTrigger value="billing">
              <CreditCard className="mr-2 h-4 w-4" />
              Billing
            </TabsTrigger>
          </TabsList>

          {/* Profile Tab */}
          <TabsContent value="profile" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Profile Information</CardTitle>
                <CardDescription>
                  Update your personal information and preferences
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                {/* Avatar */}
                <div className="flex items-center gap-4">
                  <AvatarWithName name={user?.display_name || 'User'} size="lg" imageUrl={user?.avatar_url ?? undefined} />
                  <div>
                    <input
                      type="file"
                      id="avatar-upload"
                      accept="image/jpeg,image/jpg,image/png,image/gif,image/webp"
                      className="hidden"
                      onChange={async (e) => {
                        const file = e.target.files?.[0]
                        if (!file || !user?.user_id) return

                        if (file.size > 2 * 1024 * 1024) {
                          toast.error('File size exceeds 2MB limit')
                          return
                        }

                        try {
                          setIsAvatarUploading(true)
                          const updatedUser = await settingsService.uploadAvatar(file)
                          setUser(updatedUser)
                          toast.success('Avatar updated successfully!')
                        } catch (error: any) {
                          toast.error(`Failed to upload avatar: ${error.message}`)
                        } finally {
                          setIsAvatarUploading(false)
                          e.target.value = '' // Reset file input
                        }
                      }}
                    />
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => document.getElementById('avatar-upload')?.click()}
                      disabled={isAvatarUploading}
                    >
                      {isAvatarUploading ? (
                        <>
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          Uploading...
                        </>
                      ) : (
                        'Change Photo'
                      )}
                    </Button>
                    <p className="mt-1 text-xs text-muted-foreground">
                      JPG, PNG or GIF. Max 2MB.
                    </p>
                  </div>
                </div>

                {/* Form Fields */}
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="full-name">Full Name</Label>
                    <Input
                      id="full-name"
                      value={profileForm.display_name}
                      onChange={(e) => setProfileForm(prev => ({ ...prev, display_name: e.target.value }))}
                      placeholder="John Doe"
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="email">Email Address</Label>
                    <Input
                      id="email"
                      type="email"
                      value={user?.email || ''}
                      disabled
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="role">Role</Label>
                    <Input
                      id="role"
                      defaultValue={user?.organization?.role || ''}
                      disabled
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="timezone">Timezone</Label>
                    <TimezonePicker
                      value={profileForm.timezone}
                      onChange={(tz) => setProfileForm(prev => ({ ...prev, timezone: tz }))}
                    />
                  </div>
                </div>

                <div className="flex justify-end">
                  <Button onClick={handleSaveProfile} disabled={savingProfile}>
                    {savingProfile ? (
                      <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Saving...
                      </>
                    ) : (
                      <>
                        <Save className="mr-2 h-4 w-4" />
                        Save Changes
                      </>
                    )}
                  </Button>
                </div>
              </CardContent>
            </Card>

          </TabsContent>

          {/* Organization Tab */}
          <TabsContent value="organization" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Organization Details</CardTitle>
                <CardDescription>
                  Manage your organization information and settings
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="org-name">Organization Name</Label>
                  <Input
                    id="org-name"
                    value={orgForm.name}
                    onChange={(e) => setOrgForm(prev => ({ ...prev, name: e.target.value }))}
                    placeholder="Company name"
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="org-id">Organization ID</Label>
                  <div className="flex gap-2">
                    <Input
                      id="org-id"
                      value={user?.organization?.organization_id || ''}
                      disabled
                    />
                    <Button
                      variant="outline"
                      size="icon"
                      onClick={() => {
                        navigator.clipboard.writeText(user?.organization?.organization_id || '')
                        toast.success('Organization ID copied!')
                      }}
                    >
                      <Copy className="h-4 w-4" />
                    </Button>
                  </div>
                </div>

                <div className="flex justify-end">
                  <Button onClick={handleSaveOrganization} disabled={savingOrg}>
                    {savingOrg ? (
                      <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Saving...
                      </>
                    ) : (
                      <>
                        <Save className="mr-2 h-4 w-4" />
                        Save Changes
                      </>
                    )}
                  </Button>
                </div>
              </CardContent>
            </Card>

            {/* API Keys */}
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle>API Keys</CardTitle>
                    <CardDescription>
                      Manage API keys for programmatic access
                    </CardDescription>
                  </div>
                  <Dialog open={isApiKeyDialogOpen} onOpenChange={setIsApiKeyDialogOpen}>
                    <DialogTrigger asChild>
                      <Button size="sm">
                        <Plus className="mr-2 h-4 w-4" />
                        Create Key
                      </Button>
                    </DialogTrigger>
                    <DialogContent>
                      <DialogHeader>
                        <DialogTitle>Create API Key</DialogTitle>
                        <DialogDescription>
                          Generate a new API key for your application
                        </DialogDescription>
                      </DialogHeader>
                      <div className="space-y-4">
                        <div className="space-y-2">
                          <Label htmlFor="key-name">Key Name</Label>
                          <Input
                            id="key-name"
                            placeholder="e.g., Production API Key"
                            value={apiKeyName}
                            onChange={(e) => setApiKeyName(e.target.value)}
                          />
                        </div>
                        <div className="flex justify-end gap-2">
                          <Button
                            variant="outline"
                            onClick={() => setIsApiKeyDialogOpen(false)}
                            disabled={creatingKey}
                          >
                            Cancel
                          </Button>
                          <Button onClick={handleCreateApiKey} disabled={creatingKey}>
                            {creatingKey ? (
                              <>
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                Creating...
                              </>
                            ) : (
                              <>
                                <Key className="mr-2 h-4 w-4" />
                                Create Key
                              </>
                            )}
                          </Button>
                        </div>
                      </div>
                    </DialogContent>
                  </Dialog>
                </div>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {newKeyPlaintext && (
                    <div className="rounded-lg border-2 border-primary bg-primary/5 p-4">
                      <div className="mb-2 text-sm font-medium text-primary">
                        New API Key Created — copy it now, it won't be shown again!
                      </div>
                      <div className="flex items-center gap-2">
                        <code className="flex-1 break-all rounded bg-muted px-3 py-2 font-mono text-sm">
                          {newKeyPlaintext}
                        </code>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            navigator.clipboard.writeText(newKeyPlaintext)
                            toast.success('API key copied to clipboard!')
                          }}
                        >
                          <Copy className="h-4 w-4" />
                        </Button>
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="mt-2 text-xs"
                        onClick={() => setNewKeyPlaintext(null)}
                      >
                        Dismiss
                      </Button>
                    </div>
                  )}
                  {apiKeys.length === 0 && !newKeyPlaintext && (
                    <div className="py-8 text-center text-sm text-muted-foreground">
                      No API keys yet. Create one to get started.
                    </div>
                  )}
                  {apiKeys.map((apiKey) => (
                    <div
                      key={apiKey.key_id}
                      className="flex items-center justify-between rounded-lg border border-border p-4"
                    >
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <span className="font-medium">{apiKey.name}</span>
                          {!apiKey.is_active && (
                            <Badge variant="secondary">Revoked</Badge>
                          )}
                        </div>
                        <div className="mt-1 font-mono text-sm text-muted-foreground">
                          {apiKey.key_prefix}••••••••••••
                        </div>
                        <div className="mt-1 text-xs text-muted-foreground">
                          Created: {formatDate(apiKey.created_at)}
                          {apiKey.last_used_at && ` · Last used: ${formatDate(apiKey.last_used_at)}`}
                        </div>
                      </div>
                      <div className="flex gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleRevokeApiKey(apiKey.key_id)}
                          disabled={!apiKey.is_active || revokingKeyId === apiKey.key_id}
                        >
                          {revokingKeyId === apiKey.key_id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Trash2 className="h-4 w-4 text-destructive" />
                          )}
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* Danger Zone — only visible to owners */}
            {user?.organization?.is_account_owner === true && (
              <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-6 space-y-4">
                <div className="flex items-center gap-2">
                  <AlertTriangle className="h-5 w-5 text-destructive" />
                  <h3 className="font-semibold text-destructive">Danger Zone</h3>
                </div>

                {/* Active closure banner */}
                {closureStatus && closureStatus.deletion_state === 'scheduled' && (
                  <div className="rounded-md border border-destructive bg-destructive/10 p-4 space-y-2">
                    <p className="text-sm font-medium text-destructive">
                      This account is scheduled for permanent deletion
                      {closureStatus.deletion_scheduled_for
                        ? ` on ${new Date(closureStatus.deletion_scheduled_for).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })}.`
                        : '.'}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      All data, agents, and user accounts will be permanently deleted on that date.
                      Reactivate below to cancel.
                    </p>
                  </div>
                )}

                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    {closureStatus?.deletion_state === 'scheduled' ? (
                      <>
                        <p className="text-sm font-medium">Account closure</p>
                        <p className="text-xs text-muted-foreground">
                          Your account is scheduled for deletion. You can still reactivate during the grace period.
                        </p>
                      </>
                    ) : closureStatus?.deletion_state === 'deleting' ? (
                      <>
                        <p className="text-sm font-medium">Account closure</p>
                        <p className="text-xs text-muted-foreground">
                          Deletion is in progress. This cannot be undone.
                        </p>
                      </>
                    ) : closureStatus?.deletion_state === 'deleted' ? (
                      <>
                        <p className="text-sm font-medium">Account closed</p>
                        <p className="text-xs text-muted-foreground">
                          This account has been permanently deleted.
                        </p>
                      </>
                    ) : (
                      <>
                        <p className="text-sm font-medium">Close this account</p>
                        <p className="text-xs text-muted-foreground">
                          Permanently delete this organization and all its data after a 30-day grace period.
                          This action is reversible during that window.
                        </p>
                      </>
                    )}
                  </div>
                  <div className="flex gap-2 shrink-0">
                    {closureStatus && closureStatus.deletion_state === 'scheduled' ? (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setClosureStep('reactivate')}
                        className="border-primary text-primary hover:bg-primary/10"
                      >
                        <RotateCcw className="mr-2 h-4 w-4" />
                        Reactivate account
                      </Button>
                    ) : (
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={() => setClosureStep('confirm')}
                        disabled={closureStatus?.deletion_state === 'deleting' || closureStatus?.deletion_state === 'deleted'}
                      >
                        <AlertTriangle className="mr-2 h-4 w-4" />
                        Close account
                      </Button>
                    )}
                  </div>
                </div>

                {/* Close account — confirmation dialog */}
                <Dialog open={closureStep === 'confirm'} onOpenChange={(o) => !o && setClosureStep('idle')}>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle className="text-destructive flex items-center gap-2">
                        <AlertTriangle className="h-5 w-5" />
                        Close account
                      </DialogTitle>
                      <DialogDescription>
                        This will schedule your account for permanent deletion in 30 days.
                        All agents, users, API keys, and data will be deleted.
                        You can reactivate within the grace period.
                      </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 pt-2">
                      <div className="space-y-2">
                        <Label htmlFor="confirm-org-name">
                          Type <span className="font-semibold">{organization?.name}</span> to confirm
                        </Label>
                        <Input
                          id="confirm-org-name"
                          placeholder={organization?.name}
                          value={closureForm.confirm_org_name}
                          onChange={(e) => setClosureForm(p => ({ ...p, confirm_org_name: e.target.value }))}
                        />
                      </div>
                      <div className="space-y-2">
                        <Label htmlFor="closure-reason">Reason <span className="text-muted-foreground">(optional)</span></Label>
                        <Input
                          id="closure-reason"
                          placeholder="Tell us why you're leaving (optional)"
                          value={closureForm.reason}
                          onChange={(e) => setClosureForm(p => ({ ...p, reason: e.target.value }))}
                        />
                      </div>
                      <div className="flex justify-end gap-2 pt-2">
                        <Button variant="outline" onClick={() => setClosureStep('idle')} disabled={closureLoading}>
                          Cancel
                        </Button>
                        <Button
                          variant="destructive"
                          onClick={handleCloseAccount}
                          disabled={closureLoading || closureForm.confirm_org_name !== organization?.name}
                        >
                          {closureLoading ? (
                            <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Sending...</>
                          ) : (
                            'Send confirmation email'
                          )}
                        </Button>
                      </div>
                    </div>
                  </DialogContent>
                </Dialog>

                {/* Close account — email sent state */}
                <Dialog open={closureStep === 'sent_close'} onOpenChange={(o) => !o && setClosureStep('idle')}>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle className="flex items-center gap-2">
                        Check your email
                      </DialogTitle>
                      <DialogDescription>
                        We sent a confirmation link to <span className="font-medium text-foreground">{user?.email}</span>.
                        Click the link to confirm account closure. It expires in 15 minutes.
                      </DialogDescription>
                    </DialogHeader>
                    <div className="flex justify-end pt-2">
                      <Button variant="outline" onClick={() => setClosureStep('idle')}>Done</Button>
                    </div>
                  </DialogContent>
                </Dialog>

                {/* Reactivate — confirmation dialog */}
                <Dialog open={closureStep === 'reactivate'} onOpenChange={(o) => !o && setClosureStep('idle')}>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle className="flex items-center gap-2">
                        <RotateCcw className="h-5 w-5 text-primary" />
                        Reactivate account
                      </DialogTitle>
                      <DialogDescription>
                        This will cancel the scheduled deletion and restore your account to full access.
                        We'll send a confirmation link to your email.
                      </DialogDescription>
                    </DialogHeader>
                    <div className="flex justify-end gap-2 pt-2">
                      <Button variant="outline" onClick={() => setClosureStep('idle')} disabled={closureLoading}>
                        Cancel
                      </Button>
                      <Button onClick={handleReactivateAccount} disabled={closureLoading}>
                        {closureLoading ? (
                          <><Loader2 className="mr-2 h-4 w-4 animate-spin" />Sending...</>
                        ) : (
                          'Send confirmation email'
                        )}
                      </Button>
                    </div>
                  </DialogContent>
                </Dialog>

                {/* Reactivate — email sent state */}
                <Dialog open={closureStep === 'sent_reactivate'} onOpenChange={(o) => !o && setClosureStep('idle')}>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle className="flex items-center gap-2">
                        Check your email
                      </DialogTitle>
                      <DialogDescription>
                        We sent a confirmation link to <span className="font-medium text-foreground">{user?.email}</span>.
                        Click the link to confirm reactivation. It expires in 15 minutes.
                      </DialogDescription>
                    </DialogHeader>
                    <div className="flex justify-end pt-2">
                      <Button variant="outline" onClick={() => setClosureStep('idle')}>Done</Button>
                    </div>
                  </DialogContent>
                </Dialog>
              </div>
            )}
          </TabsContent>

          {/* Team Tab */}
          <TabsContent value="team" className="space-y-4">
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle>Team Members</CardTitle>
                    <CardDescription>
                      Manage your organization's team members
                    </CardDescription>
                  </div>
                  <Dialog open={isInviteDialogOpen} onOpenChange={setIsInviteDialogOpen}>
                    <DialogTrigger asChild>
                      <Button>
                        <Plus className="mr-2 h-4 w-4" />
                        Invite Member
                      </Button>
                    </DialogTrigger>
                    <DialogContent>
                      <DialogHeader>
                        <DialogTitle>Invite Team Member</DialogTitle>
                        <DialogDescription>
                          Send an invitation to join your organization
                        </DialogDescription>
                      </DialogHeader>
                      <div className="space-y-4">
                        <div className="space-y-2">
                          <Label htmlFor="invite-email">Email Address</Label>
                          <Input
                            id="invite-email"
                            type="email"
                            placeholder="colleague@example.com"
                            value={inviteForm.email}
                            onChange={(e) => setInviteForm(prev => ({ ...prev, email: e.target.value }))}
                          />
                        </div>
                        <div className="space-y-2">
                          <Label htmlFor="invite-role">Role</Label>
                          <Select value={inviteForm.role} onValueChange={(value) => setInviteForm(prev => ({ ...prev, role: value }))}>
                            <SelectTrigger id="invite-role">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="admin">Admin</SelectItem>
                              <SelectItem value="developer">Developer</SelectItem>
                              <SelectItem value="analyst">Analyst</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                        <div className="flex justify-end gap-2">
                          <Button
                            variant="outline"
                            onClick={() => setIsInviteDialogOpen(false)}
                            disabled={invitingUser}
                          >
                            Cancel
                          </Button>
                          <Button onClick={handleInviteUser} disabled={invitingUser}>
                            {invitingUser ? (
                              <>
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                Sending...
                              </>
                            ) : (
                              'Send Invitation'
                            )}
                          </Button>
                        </div>
                      </div>
                    </DialogContent>
                  </Dialog>
                </div>
              </CardHeader>
              <CardContent>
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="border-b border-border text-left text-sm text-muted-foreground">
                        <th className="pb-3 font-medium">User</th>
                        <th className="pb-3 font-medium">Email</th>
                        <th className="pb-3 font-medium">Role</th>
                        <th className="pb-3 font-medium">Status</th>
                        <th className="pb-3 font-medium">Joined</th>
                        <th className="pb-3 font-medium">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {teamMembers.length === 0 ? (
                        <tr>
                          <td colSpan={6} className="py-8 text-center text-muted-foreground">
                            {removingUser ? 'Loading team members...' : 'No team members found'}
                          </td>
                        </tr>
                      ) : (
                        teamMembers.map((member) => (
                          <tr
                            key={member.user_id}
                            className="border-b border-border last:border-0 hover:bg-accent/50"
                          >
                            <td className="py-3">
                              <div className="flex items-center gap-3">
                                <AvatarWithName name={member.display_name ?? member.email} size="sm" />
                                <span className="font-medium">{member.display_name ?? member.email}</span>
                              </div>
                            </td>
                            <td className="py-3 text-sm text-muted-foreground">
                              {member.email}
                            </td>
                            <td className="py-3">
                              <Badge variant="secondary">{member.role}</Badge>
                            </td>
                            <td className="py-3">
                              <Badge variant="success">{member.is_active ? 'active' : 'inactive'}</Badge>
                            </td>
                            <td className="py-3 text-sm text-muted-foreground">
                              {member.joined_at ? formatDate(member.joined_at) : 'Never'}
                            </td>
                            <td className="py-3">
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() =>
                                  handleRemoveUser(member.user_id, member.display_name ?? member.email)
                                }
                                disabled={member.user_id === user?.user_id}
                              >
                                <Trash2 className="h-4 w-4 text-destructive" />
                              </Button>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* Appearance Tab */}
          <TabsContent value="appearance" className="space-y-4">
            <AppearanceSettings />
          </TabsContent>

          {/* Billing Tab */}
          <TabsContent value="billing" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle>Current Plan</CardTitle>
                <CardDescription>
                  Manage your subscription and billing information
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="rounded-lg border border-primary bg-primary/5 p-6">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="text-2xl font-bold">Professional Plan</h3>
                      <p className="mt-1 text-sm text-muted-foreground">
                        Billed monthly
                      </p>
                    </div>
                    <div className="text-right">
                      <div className="text-3xl font-bold">$99</div>
                      <div className="text-sm text-muted-foreground">/month</div>
                    </div>
                  </div>

                  <div className="mt-4 grid gap-2 text-sm">
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-1.5 rounded-full bg-primary" />
                      <span>Unlimited voice agents</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-1.5 rounded-full bg-primary" />
                      <span>10,000 minutes/month</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-1.5 rounded-full bg-primary" />
                      <span>Advanced analytics</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-1.5 rounded-full bg-primary" />
                      <span>Priority support</span>
                    </div>
                  </div>

                  <div className="mt-6 flex gap-2">
                    <Button variant="outline">Change Plan</Button>
                    <Button variant="outline">Cancel Subscription</Button>
                  </div>
                </div>

                {/* Usage */}
                <div>
                  <h4 className="mb-3 font-semibold">Current Usage</h4>
                  <div className="grid gap-3 md:grid-cols-2">
                    <div className="rounded-lg border border-border p-4">
                      <div className="text-sm text-muted-foreground">
                        Voice Minutes
                      </div>
                      <div className="mt-1 text-2xl font-bold">7,234</div>
                      <div className="mt-1 text-xs text-muted-foreground">
                        of 10,000 minutes
                      </div>
                    </div>
                    <div className="rounded-lg border border-border p-4">
                      <div className="text-sm text-muted-foreground">
                        Active Agents
                      </div>
                      <div className="mt-1 text-2xl font-bold">12</div>
                      <div className="mt-1 text-xs text-muted-foreground">
                        Unlimited
                      </div>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* Payment Method */}
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle>Payment Method</CardTitle>
                    <CardDescription>
                      Manage your payment information
                    </CardDescription>
                  </div>
                  <Button variant="outline" size="sm">
                    <Plus className="mr-2 h-4 w-4" />
                    Add Card
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                <div className="flex items-center justify-between rounded-lg border border-border p-4">
                  <div className="flex items-center gap-4">
                    <div className="flex h-12 w-12 items-center justify-center rounded bg-accent">
                      <CreditCard className="h-6 w-6" />
                    </div>
                    <div>
                      <div className="font-medium">•••• •••• •••• 4242</div>
                      <div className="text-sm text-muted-foreground">
                        Expires 12/2026
                      </div>
                    </div>
                  </div>
                  <Badge variant="success">Default</Badge>
                </div>
              </CardContent>
            </Card>

            {/* Billing History */}
            <Card>
              <CardHeader>
                <CardTitle>Billing History</CardTitle>
                <CardDescription>
                  View and download your invoices
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {[1, 2, 3].map((i) => (
                    <div
                      key={i}
                      className="flex items-center justify-between rounded-lg border border-border p-4"
                    >
                      <div>
                        <div className="font-medium">December 2025</div>
                        <div className="text-sm text-muted-foreground">
                          Invoice #INV-202512-0{i}
                        </div>
                      </div>
                      <div className="flex items-center gap-4">
                        <div className="font-medium">$99.00</div>
                        <Button variant="outline" size="sm">
                          Download
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </DashboardLayout>
  )
}
