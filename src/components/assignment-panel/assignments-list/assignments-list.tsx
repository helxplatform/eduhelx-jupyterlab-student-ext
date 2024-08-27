import React, { Fragment, useMemo, useState } from 'react'
import { Tooltip } from 'antd'
import {
    Avatar, List, ListItem, ListItemAvatar as _ListItemAvatar, ListItemText,
    ExpansionPanel, ExpansionPanelSummary, ExpansionPanelDetails
} from '@material-ui/core'
import { OpenInNewSharp, QueryBuilderOutlined, ExpandMoreSharp } from '@material-ui/icons'
import { classes } from 'typestyle'
import { assignmentBucketContainerClass, assignmentListHeaderClass, assignmentListItemClass, downloadAssignmentButtonClass } from './style'
import { TextDivider } from '../../text-divider'
import { useAssignment, useCommands } from '../../../contexts'
import type { IAssignment } from '../../../api'
import { DateFormat } from '../../../utils'
import { assignmentsListClass } from '../assignment-submissions/style'
import { disabledButtonClass } from '../../style'
import { AssignmentStatus } from '../../../api/api-responses'

const ListItemAvatar = _ListItemAvatar as any

interface ListHeaderProps {
    title: string
}

interface AssignmentListItemProps {
    assignment: IAssignment
}

interface AssignmentsBucketProps {
    title: string
    assignments?: IAssignment[]
    emptyText?: string
    defaultExpanded?: boolean
}

const ListHeader = ({ title }: ListHeaderProps) => {
    return (
        <span className={ assignmentListHeaderClass }>
            { title }
        </span>
    )
}

const AssignmentListItem = ({ assignment }: AssignmentListItemProps) => {
    const commands = useCommands()!
    return (
        <ListItem
            key={ assignment.id }
            className={ assignmentListItemClass }
            dense
            style={{
                padding: '4px 8px'
            }}
        >
            <ListItemText disableTypography>
                <div style={{ fontSize: 14, fontWeight: 500, marginBottom: 4 }}>
                    { assignment.name }
                </div>
                <div style={{ fontSize: 13, color: 'var(--jp-ui-font-color2' }}>
                    {
                        assignment.status === AssignmentStatus.UNPUBLISHED ? (
                            <span>Unpublished</span>
                        ) : assignment.status === AssignmentStatus.UPCOMING ? (
                                assignment.adjustedAvailableDate === null || assignment.adjustedDueDate === null ? (
                                    <div>
                                        Pending release
                                    </div>
                                ) : (
                                    <div style={{ display: 'flex', flexDirection: 'column' }}>
                                        <div
                                            title={ new DateFormat(assignment.adjustedAvailableDate).toBasicDatetime() }
                                        >
                                            Opens in { new DateFormat(assignment.adjustedAvailableDate).toRelativeDatetime() }
                                        </div>
                                        <div
                                            title={ new DateFormat(assignment.adjustedDueDate!).toBasicDatetime() }
                                            style={{ marginTop: 4, fontSize: 12, display: 'flex', alignItems: 'center' }}
                                        >
                                            <QueryBuilderOutlined style={{ fontSize: 16 }} />
                                            &nbsp;Lasts { new DateFormat(assignment.adjustedDueDate).toRelativeDatetime(assignment.adjustedAvailableDate) }
                                        </div>
                                    </div>
                                )
                        ) : assignment.status === AssignmentStatus.OPEN ? (
                            <span title={ new DateFormat(assignment.adjustedDueDate!).toBasicDatetime() }>
                                Closes in { new DateFormat(assignment.adjustedDueDate!).toRelativeDatetime() }
                                { assignment.isExtended && (
                                    <i>&nbsp;(extended)</i>
                                ) }
                            </span>
                        ) : assignment.status === AssignmentStatus.CLOSED ? (
                            <span title={ new DateFormat(assignment.adjustedDueDate!).toBasicDatetime() }>
                                Closed on { new DateFormat(assignment.adjustedDueDate!).toBasicDatetime() }
                            </span>
                        ) : (
                            <div>
                                Unrecognized assignment status "{ assignment.status }"
                            </div>
                        )
                    }
                </div>
            </ListItemText>
            <ListItemAvatar style={{ minWidth: 0, marginLeft: 16 }}>
                <Tooltip title={ !assignment.isPublished ? "Not available to work on yet" : undefined }>
                    <Avatar variant="square">
                        <button
                            className={ classes(downloadAssignmentButtonClass, !assignment.isPublished && disabledButtonClass) }
                            disabled={ !assignment.isPublished }
                            onClick={ () => commands.execute('filebrowser:go-to-path', {
                                path: assignment.absoluteDirectoryPath,
                                dontShowBrowser: true
                            }) }
                        >
                            <OpenInNewSharp />
                        </button>
                    </Avatar>
                </Tooltip>
            </ListItemAvatar>
        </ListItem>
    )
}

const AssignmentsBucket = ({
    title,
    assignments,
    emptyText="There are currently no assignments to work on.",
    defaultExpanded=false,
}: AssignmentsBucketProps) => {
    const [expanded, setExpanded] = useState<boolean>(defaultExpanded)

    const assignmentsSource = useMemo(() => assignments?.sort((a, b) => {
        const aDue = a.adjustedDueDate?.getTime() ?? Infinity
        const bDue = b.adjustedDueDate?.getTime() ?? Infinity
        // Sort by date, or name if same date.
        if (aDue > bDue) return 1
        if (aDue < bDue) return -1
        return a.name.localeCompare(b.name, undefined, { numeric: true })
    }), [assignments])

    const isEmpty = useMemo(() => !assignmentsSource || assignmentsSource.length === 0, [assignmentsSource])

    return (
        <ExpansionPanel
            className={ assignmentBucketContainerClass }
            square
            expanded={ expanded }
            onChange={ () => setExpanded(!expanded) }
        >
            <ExpansionPanelSummary
                expandIcon={ <ExpandMoreSharp /> }
                style={{ paddingLeft: 11 }}
            >
                <ListHeader title={ title } />
            </ExpansionPanelSummary>
            <ExpansionPanelDetails
                style={{ display: 'flex', flexDirection: 'column', paddingTop: 0, paddingLeft: 11, paddingRight: 11 }}
            >
                { !isEmpty ? (
                    assignmentsSource?.map((assignment) => (
                        <AssignmentListItem key={ assignment.id } assignment={ assignment } />
                    ))
                ) : (
                    <span style={{ color: 'var(--jp-ui-font-color1)', paddingLeft: 8 }}>
                        { emptyText }
                    </span>
                ) }
            </ExpansionPanelDetails>
        </ExpansionPanel>
    )
}

export const AssignmentsList = () => {
    const { assignments } = useAssignment()!

    const upcomingAssignments = useMemo(() => assignments?.filter((assignment) => assignment.status === AssignmentStatus.UPCOMING), [assignments])
    const activeAssignments = useMemo(() => assignments?.filter((assignment) => assignment.status === AssignmentStatus.OPEN), [assignments])
    const pastAssignments = useMemo(() => assignments?.filter((assignment) => assignment.status === AssignmentStatus.CLOSED), [assignments])

    return (
        <div style={{ flexGrow: 1, display: 'flex', flexDirection: 'column', width: 'calc(100% + 22px)' }}>
            <div className={ assignmentsListClass }>
                <AssignmentsBucket
                    title={ `Active${ activeAssignments ? " (" + activeAssignments.length + ")" : "" }` }
                    assignments={ activeAssignments }
                    emptyText="There aren't any assignments available to work on at the moment."
                    defaultExpanded={ true }
                />
                <AssignmentsBucket
                    title={ `Upcoming${ upcomingAssignments ? " (" + upcomingAssignments.length + ")" : "" }` }
                    assignments={ upcomingAssignments }
                    emptyText="There aren't any upcoming assignments right now."
                    defaultExpanded={ true }
                />
                <AssignmentsBucket
                    title={ `Past${ pastAssignments ? " (" + pastAssignments.length + ")" : "" }` }
                    assignments={ pastAssignments }
                    emptyText="There aren't any past assignments."
                    defaultExpanded={ false }
                />
            </div>
        </div>
    )
}